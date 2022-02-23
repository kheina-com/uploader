from kh_common.exceptions.http_error import BadGateway, BadRequest, Forbidden, HttpErrorHandler, InternalServerError, NotFound
from kh_common.scoring import confidence, controversial as calc_cont, hot as calc_hot
from kh_common.config.constants import posts_host, users_host
from models import Coordinates, Post, Privacy, Rating
from kh_common.sql import SqlInterface, Transaction
from aiohttp import ClientResponseError, request
from kh_common.backblaze import B2Interface
from kh_common.models.user import User
from kh_common.base64 import b64encode
from kh_common.gateway import Gateway
from os import remove as delete_file
from typing import Dict, List, Union
from kh_common.auth import KhUser
from asyncio import ensure_future
from secrets import token_bytes
from urllib.parse import quote
from exiftool import ExifTool
from wand.image import Image
from io import BytesIO
from math import floor
from uuid import uuid4
from time import time

Posts = Gateway(posts_host + '/v1/post/{post_id}', Post)
Users = Gateway(users_host + '/v1/fetch_self', User)


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
		self.emoji_size: int = 256
		self.icon_size: int = 400
		self.banner_size: int = 600
		self.output_quality: int = 85
		self.filter_function: str = 'catrom'


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


	def convert_image(self, image: Image, size: int) -> Image :
		long_side = 0 if image.size[0] > image.size[1] else 1
		ratio = size / image.size[long_side]

		if ratio < 1 :
			output_size = (floor(image.size[0] * ratio), size) if long_side else (size, floor(image.size[1] * ratio))
			image.resize(width=output_size[0], height=output_size[1], filter=self.filter_function)

		return image


	def get_image_data(self, image: Image) -> bytes :
		image.compression_quality = self.output_quality
		image_data = BytesIO()
		image.save(file=image_data)
		return image_data.getvalue()


	async def uploadImage(self, user: KhUser, file_data: bytes, filename: str, post_id:Union[str, None]=None, emoji_name:str=None) -> Dict[str, Union[str, int, List[str]]] :
		if post_id :
			self._validatePostId(post_id)

		file_on_disk = f'images/{uuid4().hex}_{filename}'.encode()
		content_type = None

		with open(file_on_disk, 'wb') as file :
			file.write(file_data)

		del file_data

		try :
			with ExifTool() as et :
				content_type = et.get_tag('File:MIMEType', file_on_disk)
				et.execute(b'-overwrite_original_in_place', b'-ALL=', file_on_disk)

		except :
			delete_file(file_on_disk)
			raise InternalServerError('Failed to strip file metadata.')

		if content_type != self._get_mime_from_filename(filename) :
			raise BadRequest('file extension does not match file type.')

		try :
			with self.transaction() as transaction :
				old_filename: List[str] = transaction.query("""
					SELECT posts.filename from kheina.public.posts
					WHERE posts.post_id = %s
					""",
					(post_id,),
					fetch_one=True,
				)

				data: List[str] = transaction.query("""
					CALL kheina.public.user_upload_file(%s, %s, %s, %s);
					""",
					(
						user.user_id,
						post_id,
						content_type,
						filename,
					),
					fetch_one=True,
				)

				with Image(file=open(file_on_disk, 'rb')) as image :
					transaction.query("""
						UPDATE kheina.public.posts
							SET width = %s,
								height = %s
						WHERE posts.post_id = %s
						""",
						image.size,
						fetch_one=True,
					)

				if not data :
					raise Forbidden('the post you are trying to upload to does not belong to this account.')

				if post_id and old_filename and old_filename[0] :
					if not await self.b2_delete_file_async(post_id, old_filename[0]) :
						self.logger.error(f'failed to delete old image: {post_id}/{old_filename[0]}')

				post_id = data[0]

				url = f'{post_id}/{filename}'

				# upload fullsize
				self.b2_upload(open(file_on_disk, 'rb').read(), url, content_type=content_type)


				# upload thumbnails
				thumbnails = { }

				for size in self.thumbnail_sizes :
					url: str = f'{post_id}/thumbnails/{size}.webp'
					with Image(file=open(file_on_disk, 'rb')) as image :
						image = self.convert_image(image, size)
						self.b2_upload(self.get_image_data(image), url, self.mime_types['webp'])

					thumbnails[size] = url

				# jpeg thumbnail
				with Image(file=open(file_on_disk, 'rb')) as image :
					url: str = f'{post_id}/thumbnails/{self.thumbnail_sizes[-1]}.jpg'
					image = self.convert_image(image, self.thumbnail_sizes[-1]).convert('jpeg')
					self.b2_upload(self.get_image_data(image), url, self.mime_types['jpeg'])

					thumbnails[size] = url

				# emoji
				emoji: str = None
				# (later)

				transaction.commit()

			delete_file(file_on_disk)

			return {
				'post_id': post_id,
				'url': url,
				'emoji': emoji,
				'thumbnails': thumbnails,
			}

		except :
			delete_file(file_on_disk)
			raise


	@HttpErrorHandler('updating post metadata')
	def updatePostMetadata(self, user: KhUser, post_id: str, title:str=None, description:str=None, privacy:Privacy=None, rating:Rating=None) -> Dict[str, Union[str, int, Dict[str, Union[None, str]]]]:
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
				params + [user.user_id, post_id],
			)

			if privacy :
				self._update_privacy(user.user_id, post_id, privacy, transaction=t, commit=True)

			else :
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


	@HttpErrorHandler('setting user icon')
	async def setIcon(self, user: KhUser, post_id: str, coordinates: Coordinates) :
		if coordinates.width != coordinates.height :
			raise BadRequest(f'icons must be square. width({coordinates.width}) != height({coordinates.height})')

		post = ensure_future(Posts(post_id=post_id))
		user = ensure_future(Users(auth=user.token.token_string))
		image = None

		post = await post

		try :
			async with request(
				'GET',
				f'https://cdn.kheina.com/file/kheina-content/{post_id}/{quote(post.filename)}',
				raise_for_status=True,
			) as response :
				image = Image(blob=await response.read())

		except ClientResponseError as e :
			raise BadGateway('unable to retrieve image from B2.', inner_exception=str(e))


		# upload new icon
		image.crop(**coordinates.dict())
		self.convert_image(image, self.icon_size)

		user = await user
		handle = user.handle.lower()

		self.b2_upload(self.get_image_data(image), f'{post_id}/icons/{handle}.webp', self.mime_types['webp'])

		image.convert('jpeg')
		self.b2_upload(self.get_image_data(image), f'{post_id}/icons/{handle}.jpg', self.mime_types['jpeg'])

		image.close()


		# update db to point to new icon
		data = await self.query_async("""
			UPDATE kheina.public.users AS users
				SET icon = %s
			FROM (SELECT icon, handle FROM kheina.public.users WHERE users.handle = LOWER(%s)) AS old
			WHERE users.handle = old.handle
				AND users.handle = LOWER(%s)
			RETURNING old.icon;
			""",
			(post_id, handle, handle),
			fetch_one=True,
			commit=True,
		)

		# cleanup old icons
		if post_id != data[0] :
			await self.b2_delete_file_async(data[0], f'icons/{handle}.webp')
			await self.b2_delete_file_async(data[0], f'icons/{handle}.jpg')


	@HttpErrorHandler('setting user banner')
	async def setBanner(self, user: KhUser, post_id: str, coordinates: Coordinates) :
		if round(coordinates.width / 3) != coordinates.height :
			raise BadRequest(f'banners must be a 3x:1 rectangle. round(width / 3)({round(coordinates.width / 3)}) != height({coordinates.height})')

		post = ensure_future(Posts(post_id=post_id))
		user = ensure_future(Users(auth=user.token.token_string))
		image = None

		post = await post

		try :
			async with request(
				'GET',
				f'https://cdn.kheina.com/file/kheina-content/{post_id}/{quote(post.filename)}',
				raise_for_status=True,
			) as response :
				image = Image(blob=await response.read())

		except ClientResponseError as e :
			raise BadGateway('unable to retrieve image from B2.', inner_exception=str(e))


		# upload new banner
		image.crop(**coordinates.dict())
		if image.size[0] > self.banner_size * 3 or image.size[1] > self.banner_size :
			image.resize(width=self.banner_size * 3, height=self.banner_size, filter=self.filter_function)

		user = await user
		handle = user.handle.lower()

		self.b2_upload(self.get_image_data(image), f'{post_id}/banners/{handle}.webp', self.mime_types['webp'])

		image.convert('jpeg')
		self.b2_upload(self.get_image_data(image), f'{post_id}/banners/{handle}.jpg', self.mime_types['jpeg'])

		image.close()


		# update db to point to new banner
		data = await self.query_async("""
			UPDATE kheina.public.users AS users
				SET banner = %s
			FROM (SELECT banner, handle FROM kheina.public.users WHERE users.handle = LOWER(%s)) AS old
			WHERE users.handle = old.handle
				AND users.handle = LOWER(%s)
			RETURNING old.banner;
			""",
			(post_id, handle, handle),
			fetch_one=True,
			commit=True,
		)

		# cleanup old banners
		if post_id != data[0] :
			await self.b2_delete_file_async(data[0], f'banners/{handle}.webp')
			await self.b2_delete_file_async(data[0], f'banners/{handle}.jpg')
