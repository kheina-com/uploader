from asyncio import ensure_future
from datetime import datetime
from enum import Enum
from io import BytesIO
from math import floor
from os import remove
from secrets import token_bytes
from time import time
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote
from uuid import UUID, uuid4

import aerospike
from aiohttp import ClientResponseError, request
from exiftool import ExifTool
from kh_common.auth import KhUser
from kh_common.backblaze import B2Interface
from kh_common.base64 import b64encode
from kh_common.caching.key_value_store import KeyValueStore
from kh_common.config.constants import posts_host, tags_host, users_host
from kh_common.exceptions.http_error import BadGateway, BadRequest, Forbidden, HttpErrorHandler, InternalServerError, NotFound
from kh_common.gateway import Gateway
from kh_common.models.privacy import Privacy
from kh_common.models.rating import Rating
from kh_common.models.user import User
from kh_common.scoring import confidence
from kh_common.scoring import controversial as calc_cont
from kh_common.scoring import hot as calc_hot
from kh_common.sql import SqlInterface, Transaction
from kh_common.utilities import flatten
from wand.image import Image

from models import Coordinates, MediaType, Post, PostSize, TagGroups


Posts: Gateway = Gateway(posts_host + '/v1/post/{post_id}', Post)
Users: Gateway = Gateway(users_host + '/v1/fetch_self', User)
Tags = Gateway(tags_host + '/v1/fetch_tags/{post_id}', TagGroups)
KVS: KeyValueStore = KeyValueStore('kheina', 'posts')
CountKVS: KeyValueStore = KeyValueStore('kheina', 'tag_count')


class Uploader(SqlInterface, B2Interface) :

	def __init__(self: 'Uploader') -> None :
		SqlInterface.__init__(
			self,
			conversions={
				Enum: lambda x: x.name,
			},
		)
		B2Interface.__init__(self, max_retries=5)
		self.thumbnail_sizes: List[int] = [
			# the length of the longest side, in pixels
			100,
			200,
			400,
			800,
			1200,
		]
		self.web_size: int = 1500
		self.emoji_size: int = 256
		self.icon_size: int = 400
		self.banner_size: int = 600
		self.output_quality: int = 85
		self.filter_function: str = 'catrom'


	def _convert_item(self: 'SqlInterface', item: Any) -> Any :
		for cls in type(item).__mro__ :
			if cls in self._conversions :
				return self._conversions[cls](item)
		return item


	def _populate_tag_cache(self, tag: str) -> None :
		if not CountKVS.exists(tag) :
			# we gotta populate it here (sad)
			data = self.query("""
				SELECT COUNT(1)
				FROM kheina.public.tags
					INNER JOIN kheina.public.tag_post
						ON tags.tag_id = tag_post.tag_id
					INNER JOIN kheina.public.posts
						ON tag_post.post_id = posts.post_id
							AND posts.privacy_id = privacy_to_id('public')
				WHERE tags.tag = %s;
				""",
				(tag,),
				fetch_one=True,
			)
			CountKVS.put(tag, int(data[0]), -1)


	def _get_tag_count(self, tag: str) -> int :
		self._populate_tag_cache(tag)
		return CountKVS.get(tag)


	def _increment_tag_count(self, tag: str) -> None :
		self._populate_tag_cache(tag)
		KeyValueStore._client.increment(
			(CountKVS._namespace, CountKVS._set, tag),
			'data',
			1,
			meta={
				'ttl': -1,
			},
			policy={
				'max_retries': 3,
			},
		)


	def _decrement_tag_count(self, tag: str) -> None :
		self._populate_tag_cache(tag)
		KeyValueStore._client.increment(
			(CountKVS._namespace, CountKVS._set, tag),
			'data',
			1,
			meta={
				'ttl': -1,
			},
			policy={
				'max_retries': 3,
			},
		)


	async def kvs_get(self: 'Uploader', post_id: str) -> Optional[Post] :
		try :
			return Post.parse_obj(await KVS.get_async(post_id))

		except aerospike.exception.RecordNotFound :
			return None


	def delete_file(self: 'Uploader', path: str) :
		try :
			remove(path)

		except FileNotFoundError :
			self.logger.exception(f'failed to delete local file, as it does not exist. path: {path}')


	def _validatePostId(self: 'Uploader', post_id: str) :
		if len(post_id) != 8 :
			raise BadRequest('the given post id is invalid.', logdata={ 'post_id': post_id })


	def _validateTitle(self: 'Uploader', title: str) :
		if title and len(title) > 100 :
			raise BadRequest('the given title is invalid, title cannot be over 100 characters in length.', logdata={ 'title': title })


	def _validateDescription(self: 'Uploader', description: str) :
		if description and len(description) > 10000 :
			raise BadRequest('the given description is invalid, description cannot be over 10,000 characters in length.', logdata={ 'description': description })


	@HttpErrorHandler('creating new post')
	def createPost(self: 'Uploader', user_id: int) -> Dict[str, Union[str, int]] :
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


	async def createPostWithFields(self: 'Uploader', user: KhUser, reply_to: str, title: str, description: str, privacy: Privacy, rating: Rating) :
		columns: List[str] = ['post_id', 'uploader']
		values: List[str] = ['%s', '%s']
		params: List[Any] = [user.user_id]

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
			params.append(rating)

		post_id = None

		with self.transaction() as transaction :
			while True :
				post_id = b64encode(token_bytes(6)).decode()
				data = transaction.query(f"SELECT count(1) FROM kheina.public.posts WHERE post_id = '{post_id}'", fetch_one=True)
				if not data[0] :
					break

			return_cols: List[str] = ['created_on', 'updated_on']

			data = transaction.query(f"""
				INSERT INTO kheina.public.posts
				({','.join(columns)})
				VALUES
				({','.join(values)})
				RETURNING {','.join(return_cols)};
				""",
				[post_id] + params,
				fetch_one=True,
			)

			if privacy :
				await self._update_privacy(user, post_id, privacy, transaction=transaction, commit=False)

			transaction.commit()

		post: Post = Post(
			user_id=user.user_id,
			privacy=privacy or Privacy.unpublished,
			**dict(zip(columns + ['created', 'updated'], [post_id] + params + list(data))),
		)

		# store this post in cache
		KVS.put(post_id, post.dict())

		return {
			'post_id': post_id,
		}


	def convert_image(self: 'Uploader', image: Image, size: int) -> Image :
		long_side = 0 if image.size[0] > image.size[1] else 1
		ratio = size / image.size[long_side]

		if ratio < 1 :
			output_size = (floor(image.size[0] * ratio), size) if long_side else (size, floor(image.size[1] * ratio))
			image.resize(width=output_size[0], height=output_size[1], filter=self.filter_function)

		return image


	def get_image_data(self: 'Uploader', image: Image, compress: bool = True) -> bytes :
		if compress :
			image.compression_quality = self.output_quality

		image_data = BytesIO()
		image.save(file=image_data)
		return image_data.getvalue()


	async def uploadImage(
		self: 'Uploader',
		user: KhUser,
		file_data: bytes,
		filename: str,
		post_id: Union[str, None] = None,
		emoji_name: str = None,
		web_resize: bool = None,
	) -> Dict[str, Union[str, int, List[str]]] :
		if post_id :
			self._validatePostId(post_id)

		# validate it's an actual photo
		with Image(blob=file_data) as image :
			pass

		file_on_disk: bytes = f'images/{uuid4().hex}_{filename}'.encode()

		with open(file_on_disk, 'wb') as file :
			file.write(file_data)

		del file_data
		content_type: str

		try :
			with ExifTool() as et :
				content_type = et.get_tag('File:MIMEType', file_on_disk)
				et.execute(b'-overwrite_original_in_place', b'-ALL=', file_on_disk)

		except :
			self.delete_file(file_on_disk)
			refid: UUID = uuid4()
			self.logger.exception({ 'refid': refid })
			raise InternalServerError('Failed to strip file metadata.', refid=refid)

		if content_type != self._get_mime_from_filename(filename.lower()) :
			self.delete_file(file_on_disk)
			raise BadRequest('file extension does not match file type.')

		if web_resize :
			dot_index: int = filename.rfind('.')

			if dot_index and filename[dot_index + 1:] in self.mime_types :
				filename = filename[:dot_index] + '-web' + filename[dot_index:]

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

				if not data :
					raise Forbidden('the post you are trying to upload to does not belong to this account.')

				fullsize_image: bytes

				with Image(file=open(file_on_disk, 'rb')) as image :
					if web_resize :
						image: Image = self.convert_image(image, self.web_size)
						fullsize_image = self.get_image_data(image, compress = False)

					# optimize
					updated: Tuple[datetime] = transaction.query("""
						UPDATE kheina.public.posts
							SET width = %s,
								height = %s
						WHERE posts.post_id = %s
						RETURNING posts.updated_on;
						""",
						(*image.size, post_id),
						fetch_one=True,
					)
					updated: datetime = updated[0]
					image_size: PostSize = PostSize(
						width=image.size[0],
						height=image.size[1],
					)

				if post_id and old_filename and old_filename[0] :
					if not await self.b2_delete_file_async(f'{post_id}/{old_filename[0]}') :
						self.logger.error(f'failed to delete old image: {post_id}/{old_filename[0]}')

				post_id: str = data[0]
				url: str = f'{post_id}/{filename}'

				if not web_resize :
					# this would have been populated earlier, if resized
					fullsize_image = open(file_on_disk, 'rb').read()

				# upload fullsize
				self.b2_upload(fullsize_image, url, content_type=content_type)

				del fullsize_image

				# upload thumbnails
				thumbnails = { }

				for size in self.thumbnail_sizes :
					thumbnail_url: str = f'{post_id}/thumbnails/{size}.webp'
					with Image(file=open(file_on_disk, 'rb')) as image :
						image = self.convert_image(image, size)
						self.b2_upload(self.get_image_data(image), thumbnail_url, self.mime_types['webp'])

					thumbnails[size] = thumbnail_url

				# jpeg thumbnail
				with Image(file=open(file_on_disk, 'rb')) as image :
					thumbnail_url: str = f'{post_id}/thumbnails/{self.thumbnail_sizes[-1]}.jpg'
					image = self.convert_image(image, self.thumbnail_sizes[-1]).convert('jpeg')
					self.b2_upload(self.get_image_data(image), thumbnail_url, self.mime_types['jpeg'])

					thumbnails['jpeg'] = thumbnail_url

				# TODO: implement emojis
				emoji: str = None

				transaction.commit()

			post: Optional[Post] = await self.kvs_get(post_id)
			if post :
				# post is populated in cache, so we can safely update it
				post.updated = updated
				post.media_type = MediaType(
					file_type=content_type[content_type.find('/')+1:],
					mime_type=content_type,
				)
				post.size = image_size
				post.filename = filename
				KVS.put(post_id, post.dict())

			return {
				'post_id': post_id,
				'url': url,
				'emoji': emoji,
				'thumbnails': thumbnails,
			}

		finally :
			self.delete_file(file_on_disk)


	@HttpErrorHandler('updating post metadata')
	async def updatePostMetadata(self: 'Uploader', user: KhUser, post_id: str, title:str=None, description:str=None, privacy:Privacy=None, rating:Rating=None) -> Dict[str, Union[str, int, Dict[str, Union[None, str]]]]:
		self._validatePostId(post_id)
		self._validateTitle(title)
		self._validateDescription(description)

		query = """
			UPDATE kheina.public.posts
			SET updated_on = NOW()
			"""

		columns: List[str] = []
		params: List[Any] = []

		if title is not None :
			query += """,
			title = %s"""
			columns.append('title')
			params.append(title or None)

		if description is not None :
			query += """,
			description = %s"""
			columns.append('description')
			params.append(description or None)

		if rating :
			query += """,
			rating = rating_to_id(%s)"""
			columns.append('rating')
			params.append(rating)

		if not params :
			raise BadRequest('no params were provided.')

		with self.transaction() as t :
			return_cols: List[str] = ['created_on', 'updated_on']

			data = t.query(
				query + f"""
				WHERE uploader = %s
					AND post_id = %s
				RETURNING {','.join(return_cols)};
				""",
				params + [user.user_id, post_id],
				fetch_one=True,
			)

			if privacy :
				await self._update_privacy(user, post_id, privacy, transaction=t, commit=True)

			else :
				t.commit()

		post: Optional[Post] = await self.kvs_get(post_id)
		if post :
			# post is populated in cache, so we can safely update it

			if privacy :
				post.privacy = privacy

			post = Post.parse_obj({
				**post.dict(),
				**dict(zip(columns + ['created', 'updated'], params + list(data))),
			})

			KVS.put(post_id, post.dict())

		return True


	async def _update_privacy(self: 'Uploader', user: KhUser, post_id: str, privacy: Privacy, transaction: Transaction = None, commit: bool = True) :
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
				(user.user_id, post_id),
				fetch_one=True,
			)

			if not data :
				raise NotFound('the provided post does not exist or it does not belong to this account.')

			if data[0] == privacy.name :
				raise BadRequest('post privacy cannot be updated to the current privacy level.')

			tags = Tags(post_id=post_id, auth=user.token.token_string if user.token else None)

			if data[0] == Privacy.unpublished.name :
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
					user.user_id, post_id, True,
					post_id, 1, 0, 1, calc_hot(1, 0, time()), confidence(1, 1), calc_cont(1, 0),
					privacy.name, user.user_id, post_id,
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
					privacy.name, user.user_id, post_id,
				)

			t.query(query, params)

			if commit :
				t.commit()

			if privacy == Privacy.public :
				for tag in filter(None, flatten((await tags).dict())) :
					self._increment_tag_count(tag)

			else :
				for tag in filter(None, flatten((await tags).dict())) :
					self._decrement_tag_count(tag)

		return True


	@HttpErrorHandler('updating post privacy')
	async def updatePrivacy(self: 'Uploader', user: KhUser, post_id: str, privacy: Privacy) :
		await self._update_privacy(user, post_id, privacy)

		post: Optional[Post] = await self.kvs_get(post_id)
		if post :
			post.privacy = privacy
			KVS.put(post_id, post.dict())


	@HttpErrorHandler('setting user icon')
	async def setIcon(self: 'Uploader', user: KhUser, post_id: str, coordinates: Coordinates) :
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
			FROM (SELECT icon, handle FROM kheina.public.users WHERE LOWER(users.handle) = LOWER(%s)) AS old
			WHERE users.handle = old.handle
				AND LOWER(users.handle) = LOWER(%s)
			RETURNING old.icon;
			""",
			(post_id, handle, handle),
			fetch_one=True,
			commit=True,
		)

		# cleanup old icons
		if post_id != data[0] :
			await self.b2_delete_file_async(f'{data[0]}/icons/{handle}.webp')
			await self.b2_delete_file_async(f'{data[0]}/icons/{handle}.jpg')


	@HttpErrorHandler('setting user banner')
	async def setBanner(self: 'Uploader', user: KhUser, post_id: str, coordinates: Coordinates) :
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
			await self.b2_delete_file_async(f'{data[0]}/banners/{handle}.webp')
			await self.b2_delete_file_async(f'{data[0]}/banners/{handle}.jpg')
