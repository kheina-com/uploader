from kh_common.exceptions.http_error import BadRequest, Forbidden, HttpError, HttpErrorHandler, InternalServerError, NotFound
from kh_common.scoring import confidence, controversial as calc_cont, hot as calc_hot
from kh_common.sql import SqlInterface, Transaction
from kh_common.config.repo import name, short_hash
from asyncio import coroutine, ensure_future
from kh_common.backblaze import B2Interface
from kh_common.logging import getLogger
from kh_common.base64 import b64encode
from typing import Dict, List, Union
from models import Privacy, Rating
from kh_common.auth import KhUser
from secrets import token_bytes
from io import BytesIO
from math import floor
from uuid import uuid4
from time import time
from PIL import Image


class Uploader(SqlInterface, B2Interface) :

	def __init__(self) -> None :
		SqlInterface.__init__(self)
		B2Interface.__init__(self, max_retries=5)
		self.thumbnail_sizes: List[int] = [
			# the length of the longest side, in pixels
			100,
			200,
			400,
			800,
			1200,
		]
		self.resample_function: int = Image.BICUBIC


	def _validatePostId(self, post_id: str) :
		if len(post_id) != 8 :
			raise BadRequest('the given post id is invalid.', logdata={ 'post_id': post_id })


	def _validateTitle(self, title: str) :
		if title and len(title) > 100 :
			raise BadRequest('the given title is invalid, title cannot be over 100 characters in length.', logdata={ 'title': title })


	def _validateDescription(self, description: str) :
		if description and len(description) > 10000 :
			raise BadRequest('the given description is invalid, description cannot be over 10,000 characters in length.', logdata={ 'description': description })


	@HttpErrorHandler('creating new post')
	def createPost(self, user_id: int) -> Dict[str, Union[str, int]] :
		data: List[str] = self.query("""
			SELECT kheina.public.create_new_post(%s);
			""",
			(user_id,),
			commit=True,
			fetch_one=True,
		)

		return {
			'user_id': user_id,
			'post_id': data[0],
		}


	def createPostWithFields(self, user: KhUser, reply_to: str, title: str, description: str, privacy: Privacy, rating: Rating) :
		columns = ['post_id', 'uploader']
		values = ['%s', '%s']
		params = [user.user_id]

		if reply_to :
			self._validatePostId(reply_to)
			columns.append('parent')
			values.append('%s')
			params.append(reply_to)

		if title :
			self._validateTitle(title)
			columns.append('title')
			values.append('%s')
			params.append(title)

		if description :
			self._validateDescription(description)
			columns.append('description')
			values.append('%s')
			params.append(description)

		if rating :
			columns.append('rating')
			values.append('rating_to_id(%s)')
			params.append(rating.name)

		post_id = None

		with self.transaction() as transaction :
			while True :
				post_id = b64encode(token_bytes(6)).decode()
				data = transaction.query(f"SELECT count(1) FROM kheina.public.posts WHERE post_id = '{post_id}'", fetch_one=True)
				if not data[0] :
					break

			transaction.query(f"""
				INSERT INTO kheina.public.posts
				({','.join(columns)})
				VALUES
				({','.join(values)})
				""",
				[post_id] + params,
			)

			if privacy :
				self._update_privacy(user.user_id, post_id, privacy, transaction=transaction, commit=False)

			transaction.commit()

		return {
			'post_id': post_id,
		}


	async def uploadJpegBackup(self, post_id: str, thumbnail_data: BytesIO) :
		jpeg = Image.open(thumbnail_data)

		if jpeg.mode != 'RGB' :
			background = Image.new('RGBA', jpeg.size, (255,255,255))
			jpeg = Image.alpha_composite(background, jpeg.convert('RGBA'))
			del background

		jpeg = jpeg.convert('RGB')
		thumbnail_data = BytesIO()
		jpeg.save(thumbnail_data, format='JPEG', quality=85)

		thumbnail_url = f'{post_id}/thumbnails/{self.thumbnail_sizes[-1]}.jpg'

		self.b2_upload(thumbnail_data.getvalue(), thumbnail_url, self.mime_types['jpeg'])

		return thumbnail_url


	async def uploadImage(self, user_id: int, file_data: bytes, filename: str, post_id:Union[str, None]=None) -> Dict[str, Union[str, int, List[str]]] :
		if post_id :
			self._validatePostId(post_id)

		try :
			# we can't just open this once cause PIL sucks
			Image.open(BytesIO(file_data)).verify()

		except Exception as e :
			refid: str = uuid4().hex
			logdata = {
				'refid': refid,
				'user_id': user_id,
				'filename': filename,
				'error': str(e),
			}
			self.logger.warning(logdata)
			raise BadRequest('user image failed validation.', logdata=logdata)

		url = None
		thumbnails = None
		logdata = None

		try :

			image = Image.open(BytesIO(file_data))
			content_type: str = f'image/{image.format.lower()}'

			if content_type != self._get_mime_from_filename(filename) :
				raise BadRequest('file extension does not match file type.')

			stripped_image = Image.new(image.mode, image.size)
			stripped_image.putdata(image.getdata())
			image = stripped_image
			del stripped_image

			file_data = BytesIO()
			file_data.name = filename
			image.save(file_data)
			file_data = file_data.getvalue()

			data: List[str] = self.query("""
				CALL kheina.public.user_upload_file(%s, %s, %s, %s);
				""",
				(
					user_id,
					post_id,
					content_type,
					filename,
				),
				commit=True,
				fetch_one=True,
			)

			if not data :
				raise Forbidden('the post you are trying to upload to does not belong to this account.')

			if post_id :
				old_filename: List[str] = self.query("""
					SELECT posts.filename from kheina.public.posts
					WHERE posts.post_id = %s
					""",
					(post_id,),
					fetch_one=True,
				)

				if old_filename and old_filename[0] :
					await self.b2_delete_file_async(f'{post_id}/{old_filename[0]}')

			post_id = data[0]

			url = f'{post_id}/{filename}'
			logdata = {
				'url': url,
				'filename': filename,
				'image': 'full size',
				'color': image.mode,
				'type': image.format,
				'animated': getattr(image, 'is_animated', False),
			}

			# upload the raw file
			self.b2_upload(file_data, url, content_type=content_type)

			# render all thumbnails and queue them for upload async.
			# I'm back, async doesn't work with large files.
			long_side = 0 if image.size[0] > image.size[1] else 1

			image = image.convert('RGBA')

			thumbnails = { }

			thumbnail_data = None
			max_size = False
			for size in self.thumbnail_sizes :
				thumbnail_url = f'{post_id}/thumbnails/{size}.webp'
				thumbnails[size] = thumbnail_url
				logdata['image'] = f'thumbnail {size}'
				logdata['url'] = thumbnail_url
				ratio = size / image.size[long_side]

				if ratio < 1 :
					# resize and output
					thumbnail_data = BytesIO()
					output_size = (floor(image.size[0] * ratio), size) if long_side else (size, floor(image.size[1] * ratio))
					image.resize(output_size, resample=self.resample_function).save(thumbnail_data, format='WEBP', quality=85)

				elif not thumbnail_data or not max_size :
					# just convert what we have
					thumbnail_data = BytesIO()
					image.save(thumbnail_data, format='WEBP', quality=85)
					max_size = True

				self.b2_upload(thumbnail_data.getvalue(), thumbnail_url, self.mime_types['webp'])

			# finally, the jpeg backup
			thumbnails['jpeg'] = await self.uploadJpegBackup(post_id, thumbnail_data)

			return {
				'user_id': user_id,
				'post_id': post_id,
				'url': url,
				'thumbnails': thumbnails,
			}

		except Exception as e :
			refid: str = uuid4().hex
			self.logger.critical({
					'refid': refid,
					'error': str(e),
					'message': 'an unexpected error occurred while uploading image to backblaze.',
					'thumbnails': thumbnails,
					**logdata,
				},
				exc_info=e,
			)
			raise InternalServerError(
				'an unexpected error occurred while uploading image to cdn.',
				refid=refid,
			)


	@HttpErrorHandler('updating post metadata')
	def updatePostMetadata(self, user_id: int, post_id: str, title:str=None, description:str=None, privacy:Privacy=None, rating:Rating=None) -> Dict[str, Union[str, int, Dict[str, Union[None, str]]]]:
		self._validatePostId(post_id)
		self._validateTitle(title)
		self._validateDescription(description)

		query = """
			UPDATE kheina.public.posts
			SET updated_on = NOW()
			"""

		params = []

		if title :
			query += """,
			title = %s"""
			params.append(title)

		if description :
			query += """,
			description = %s"""
			params.append(description)

		if rating :
			query += """,
			rating = rating_to_id(%s)"""
			params.append(rating.name)

		if not params :
			raise BadRequest('no params were provided.')

		with self.transaction() as t :
			t.query(
				query + """
				WHERE uploader = %s
					AND post_id = %s;
				""",
				params + [user_id, post_id],
			)

			if privacy :
				self._update_privacy(user_id, post_id, privacy, transaction=t, commit=False)
			
			t.commit()

		return True


	def _update_privacy(self, user_id: int, post_id: str, privacy: Privacy, transaction: Transaction = None, commit: bool = True) :
		self._validatePostId(post_id)

		with transaction or self.transaction() as t :
			data = t.query("""
				SELECT privacy.type
				FROM kheina.public.posts
					INNER JOIN kheina.public.privacy
						ON posts.privacy_id = privacy.privacy_id
				WHERE posts.uploader = %s
					AND posts.post_id = %s;
				""",
				(user_id, post_id),
				fetch_one=True,
			)

			if not data :
				raise NotFound('the provided post does not exist or it does not belong to this account.')

			if data[0] == 'unpublished' :
				query = """
					INSERT INTO kheina.public.post_votes
					(user_id, post_id, upvote)
					VALUES
					(%s, %s, %s)
					ON CONFLICT DO NOTHING;

					INSERT INTO kheina.public.post_scores
					(post_id, upvotes, downvotes, top, hot, best, controversial)
					VALUES
					(%s, %s, %s, %s, %s, %s, %s)
					ON CONFLICT DO NOTHING;

					UPDATE kheina.public.posts
						SET created_on = NOW(),
							updated_on = NOW(),
							privacy_id = privacy_to_id(%s)
					WHERE posts.uploader = %s
						AND posts.post_id = %s;
				"""
				params = (
					user_id, post_id, True,
					post_id, 1, 0, 1, calc_hot(1, 0, time()), confidence(1, 1), calc_cont(1, 0),
					privacy.name, user_id, post_id,
				)

			else :
				query = """
					UPDATE kheina.public.posts
						SET updated_on = NOW(),
							privacy_id = privacy_to_id(%s)
					WHERE posts.uploader = %s
						AND posts.post_id = %s;
				"""
				params = (
					privacy.name, user_id, post_id,
				)

			t.query(query, params)

			if commit :
				t.commit()

		return True
	
	@HttpErrorHandler('updating post privacy')
	def updatePrivacy(self, user_id: int, post_id: str, privacy: Privacy) :
		self._update_privacy(user_id, post_id, privacy)
