from kh_common.exceptions.http_error import BadRequest, Forbidden, HttpError, HttpErrorHandler, InternalServerError
from kh_common.scoring import confidence, controversial as calc_cont, hot as calc_hot
from kh_common.config.repo import name, short_hash
from kh_common.backblaze import B2Interface
from kh_common.logging import getLogger
from kh_common.sql import SqlInterface
from typing import Dict, List, Union
from asyncio import coroutine
from models import Privacy
from io import BytesIO
from math import floor
from uuid import uuid4
from time import time
from PIL import Image


class Uploader(SqlInterface, B2Interface) :

	def __init__(self) -> None :
		SqlInterface.__init__(self)
		B2Interface.__init__(self, max_retries=100)
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


	def _validatePrivacy(self, privacy: Privacy) :
		if privacy == Privacy.unpublished :
			raise BadRequest('you cannot set a post to unpublished.')


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


	async def uploadImage(self, user_id: int, file_data: bytes, filename: str, post_id:Union[str, type(None)]=None) -> Dict[str, Union[str, int, List[str]]] :
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

		try :

			image = Image.open(BytesIO(file_data))
			content_type: str = self._get_mime_from_filename(image.format.lower())

			if content_type != self._get_mime_from_filename(filename) :
				raise BadRequest('file extension does not match file type.')

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

			post_id = data[0]

			url = f'{post_id}/{filename}'
			logdata = {
				'user_id': user_id,
				'post_id': post_id,
				'url': url,
				'filename': filename,
				'image': 'full size',
				'color': image.mode,
				'type': image.format,
				'animated': getattr(image, 'is_animated', False),
			}

			uploads: List[coroutine] = []

			# upload the raw file
			uploads.append(self.b2_upload_async(file_data, url, content_type=content_type))

			# render all thumbnails and queue them for upload async
			if image.mode != 'RGB' :
				background = Image.new('RGBA', image.size, (255,255,255))
				image = Image.alpha_composite(background, image.convert('RGBA'))
				del background

			image = image.convert('RGB')
			long_side = 0 if image.size[0] > image.size[1] else 1

			thumbnails = {}

			thumbnail_data = None
			max_size = False
			for size in self.thumbnail_sizes :
				thumbnail_url = f'{post_id}/thumbnails/{size}.jpg'
				logdata['image'] = f'thumbnail {size}'
				logdata['url'] = thumbnail_url
				ratio = size / image.size[long_side]

				if ratio < 1 :
					# resize and output
					thumbnail_data = BytesIO()
					output_size = (floor(image.size[0] * ratio), size) if long_side else (size, floor(image.size[1] * ratio))
					thumbnail = image.resize(output_size, resample=self.resample_function).save(thumbnail_data, format='JPEG', quality=60)

				elif not thumbnail_data or not max_size :
					# just convert what we have
					thumbnail_data = BytesIO()
					thumbnail = image.save(thumbnail_data, format='JPEG', quality=60)
					max_size = True

				uploads.append(self.b2_upload_async(thumbnail_data.getvalue(), thumbnail_url, self.mime_types['jpeg']))
				thumbnails[size] = thumbnail_url

			for upload in uploads :
				await upload
		
		except Exception as e :
			self.logger.critical(
				'an unexpected error occurred while uploading image to backblaze.',
				exc_info=e,
				user_id=user_id,
				post_id=post_id,
				url=url,
				thumbnails=thumbnails,
				logdata=logdata,
			)

		return {
			'user_id': user_id,
			'post_id': post_id,
			'url': url,
			'thumbnails': thumbnails,
		}


	@HttpErrorHandler('updating post metadata')
	def updatePostMetadata(self, user_id: int, post_id: str, title:str=None, description:str=None) -> Dict[str, Union[str, int, Dict[str, Union[None, str]]]]:
		self._validatePostId(post_id)

		query = """
			UPDATE kheina.public.posts
			SET updated_on = NOW()
			"""

		params = []
		return_data = { }

		if title :
			query += """,
			title = %s"""
			params.append(title)
			return_data['title'] = title

		if description :
			query += """,
			description = %s"""
			params.append(description)
			return_data['description'] = description

		if not params :
			raise BadRequest('no params were provided.')

		data = self.query(
			query + """
			WHERE uploader = %s
				AND post_id = %s;
			""",
			params + [user_id, post_id],
			commit=True,
		)

		return {
			'user_id': user_id,
			'post_id': post_id,
			'data': return_data,
		}


	@HttpErrorHandler('updating post privacy')
	def updatePrivacy(self, user_id: int, post_id: str, privacy: Privacy) :
		self._validatePostId(post_id)
		self._validatePrivacy(privacy)

		with self.transaction() as transaction :
			data = transaction.query("""
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
				raise BadRequest('the provided post does not exist or it does not belong to this account.')


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
						SET created_on = NOW(),
							updated_on = NOW(),
							privacy_id = privacy_to_id(%s)
					WHERE posts.uploader = %s
						AND posts.post_id = %s;
				"""
				params = (
					privacy.name, user_id, post_id,
				)

			transaction.query(query, params)
			transaction.commit()

		return {
			post_id: {
				'privacy': privacy.name,
			},
		}