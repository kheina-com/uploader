from asyncio import Task, ensure_future
from datetime import datetime
from enum import Enum
from io import BytesIO
from math import floor
from os import makedirs, path, remove
from secrets import token_bytes
from time import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import quote
from uuid import UUID, uuid4

import aerospike
from aiohttp import ClientResponseError, request
from exiftool import ExifTool
from fuzzly.internal import InternalClient
from fuzzly.models.internal import InternalPost, InternalUser, UserKVS
from fuzzly.models.post import MediaType, Post, PostId, PostSize, Privacy, Rating
from fuzzly.models.tag import TagGroups
from kh_common.auth import KhUser
from kh_common.backblaze import B2Interface
from kh_common.caching.key_value_store import KeyValueStore
from kh_common.config.credentials import fuzzly_client_token
from kh_common.exceptions.http_error import BadGateway, BadRequest, Forbidden, HttpErrorHandler, InternalServerError, NotFound
from kh_common.sql import SqlInterface, Transaction
from kh_common.utilities import flatten, int_from_bytes
from wand.image import Image

from models import Coordinates
from scoring import confidence
from scoring import controversial as calc_cont
from scoring import hot as calc_hot


KVS: KeyValueStore = KeyValueStore('kheina', 'posts')
CountKVS: KeyValueStore = KeyValueStore('kheina', 'tag_count')
UnpublishedPrivacies: Set[Privacy] = { Privacy.unpublished, Privacy.draft }
client: InternalClient = InternalClient(fuzzly_client_token)


if not path.isdir('images') :
	makedirs('images')


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


	async def _populate_tag_cache(self, tag: str) -> None :
		if not await CountKVS.exists_async(tag) :
			# we gotta populate it here (sad)
			data = await self.query_async("""
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
			await CountKVS.put_async(tag, int(data[0]), -1)


	async def _get_tag_count(self, tag: str) -> int :
		await self._populate_tag_cache(tag)
		return await CountKVS.get_async(tag)


	async def _increment_total_post_count(self, value: int = 1) -> None :
		if not await CountKVS.exists_async('_') :
			# we gotta populate it here (sad)
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.privacy_id = privacy_to_id('public');
				""",
				fetch_one=True,
			)
			await CountKVS.put_async('_', int(data[0]) + value, -1)

		else :
			KeyValueStore._client.increment(
				(CountKVS._namespace, CountKVS._set, '_'),
				'data',
				value,
				meta={
					'ttl': -1,
				},
				policy={
					'max_retries': 3,
				},
			)


	async def _increment_user_count(self, user_id: int, value: int = 1) -> None :
		if not await CountKVS.exists_async(f'@{user_id}') :
			# we gotta populate it here (sad)
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.uploader = %s
					AND posts.privacy_id = privacy_to_id('public');
				""",
				(user_id,),
				fetch_one=True,
			)
			await CountKVS.put_async('_', int(data[0]) + value, -1)

		else :
			KeyValueStore._client.increment(
				(CountKVS._namespace, CountKVS._set, f'@{user_id}'),
				'data',
				value,
				meta={
					'ttl': -1,
				},
				policy={
					'max_retries': 3,
				},
			)


	async def _increment_rating_count(self, rating: Rating, value: int = 1) -> None :
		if not await CountKVS.exists_async(rating.name) :
			# we gotta populate it here (sad)
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.rating = rating_to_id(%s)
					AND posts.privacy_id = privacy_to_id('public');
				""",
				(rating,),
				fetch_one=True,
			)
			await CountKVS.put_async('_', int(data[0]) + value, -1)

		else :
			KeyValueStore._client.increment(
				(CountKVS._namespace, CountKVS._set, rating.name),
				'data',
				value,
				meta={
					'ttl': -1,
				},
				policy={
					'max_retries': 3,
				},
			)


	async def _increment_tag_count(self, tag: str, value: int = 1) -> None :
		await self._populate_tag_cache(tag)
		KeyValueStore._client.increment(
			(CountKVS._namespace, CountKVS._set, tag),
			'data',
			value,
			meta={
				'ttl': -1,
			},
			policy={
				'max_retries': 3,
			},
		)


	async def kvs_get(self: 'Uploader', post_id: PostId) -> Optional[InternalPost] :
		try :
			return await KVS.get_async(post_id)

		except aerospike.exception.RecordNotFound :
			return None


	def delete_file(self: 'Uploader', path: str) :
		try :
			remove(path)

		except FileNotFoundError :
			self.logger.exception(f'failed to delete local file, as it does not exist. path: {path}')


	def _validateTitle(self: 'Uploader', title: str) :
		if title and len(title) > 100 :
			raise BadRequest('the given title is invalid, title cannot be over 100 characters in length.', logdata={ 'title': title })


	def _validateDescription(self: 'Uploader', description: str) :
		if description and len(description) > 10000 :
			raise BadRequest('the given description is invalid, description cannot be over 10,000 characters in length.', logdata={ 'description': description })


	@HttpErrorHandler('creating new post')
	async def createPost(self: 'Uploader', user: KhUser) -> Dict[str, Union[str, int]] :
		with self.transaction() as transaction :
			post_id: int

			while True :
				post_id = int_from_bytes(token_bytes(6))
				data = transaction.query("SELECT count(1) FROM kheina.public.posts WHERE post_id = %s;", (post_id,), fetch_one=True)
				if not data[0] :
					break

			data: List[str] = transaction.query("""
				INSERT INTO kheina.public.posts
				(post_id, uploader, privacy_id)
				VALUES
				(%s, %s, privacy_to_id('unpublished'))
				ON CONFLICT (uploader, privacy_id) WHERE privacy_id = 4 DO NOTHING;

				SELECT post_id FROM kheina.public.posts
				WHERE uploader = %s
					AND privacy_id = privacy_to_id('unpublished');
				""",
				(post_id, user.user_id, user.user_id),
				fetch_one=True,
			)

			transaction.commit()

		return {
			'user_id': user.user_id,
			'post_id': PostId(data[0]),
		}


	async def createPostWithFields(self: 'Uploader', user: KhUser, reply_to: PostId, title: str, description: str, privacy: Privacy, rating: Rating) :
		columns: List[str] = ['post_id', 'uploader']
		values: List[str] = ['%s', '%s']
		params: List[Any] = [user.user_id]
		uploader: Task[InternalUser] = ensure_future(client.user(user.user_id))

		post: InternalPost = InternalPost(
			post_id=reply_to,
			user_id=user.user_id,
			user=(await uploader).handle,
			rating=Rating.explicit,
			privacy=Privacy.public,
		)

		if reply_to :
			internal_reply_to: int = self._validatePostId(reply_to)
			columns.append('parent')
			values.append('%s')
			params.append(internal_reply_to)
			post.parent = reply_to.int()

		if title :
			self._validateTitle(title)
			columns.append('title')
			values.append('%s')
			params.append(title)
			post.title = title

		if description :
			self._validateDescription(description)
			columns.append('description')
			values.append('%s')
			params.append(description)
			post.description = description

		if rating :
			columns.append('rating')
			values.append('rating_to_id(%s)')
			params.append(rating)
			post.rating = rating

		internal_post_id: int
		post_id: PostId

		with self.transaction() as transaction :
			while True :
				internal_post_id = int_from_bytes(token_bytes(6))
				data = transaction.query("SELECT count(1) FROM kheina.public.posts WHERE post_id = %s;", (internal_post_id,), fetch_one=True)
				if not data[0] :
					break

			return_cols: List[str] = ['created_on', 'updated_on']

			data = transaction.query(f"""
				INSERT INTO kheina.public.posts
				(privacy_id, {','.join(columns)})
				VALUES
				(privacy_to_id('draft'), {','.join(values)})
				RETURNING {','.join(return_cols)};
				""",
				[internal_post_id] + params,
				fetch_one=True,
			)

			post_id = PostId(internal_post_id)

			if privacy :
				await self._update_privacy(user, post_id, privacy, transaction=transaction, commit=False)
				post.privacy = privacy

			transaction.commit()

		post.post_id = post_id.int()
		KVS.put(post_id, post)

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
		post_id: PostId,
		emoji_name: str = None,
		web_resize: int = 0,
	) -> Dict[str, Union[str, int, List[str]]] :
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

			if dot_index and filename[dot_index + 1:].lower() in self.mime_types :
				filename = filename[:dot_index] + '-web' + filename[dot_index:]

		try :
			with self.transaction() as transaction :
				data: List[str] = transaction.query("""
					SELECT posts.filename from kheina.public.posts
					WHERE posts.post_id = %s
						AND uploader = %s;
					""",
					(post_id.int(), user.user_id),
					fetch_one=True,
				)

				# if the user owns the above post, then data should always be populated, even if it's just [None]
				if not data :
					raise Forbidden('the post you are trying to upload to does not belong to this account.')

				old_filename: str = data[0]
				fullsize_image: bytes

				with Image(file=open(file_on_disk, 'rb')) as image :
					if web_resize :
						image: Image = self.convert_image(image, web_resize)
						fullsize_image = self.get_image_data(image, compress = False)

					# optimize
					updated: Tuple[datetime] = transaction.query("""
						UPDATE kheina.public.posts
							SET updated_on = NOW(),
								media_type_id = media_mime_type_to_id(%s),
								filename = %s,
								width = %s,
								height = %s
						WHERE posts.post_id = %s
							AND posts.uploader = %s
						RETURNING posts.updated_on;
						""",
						(
							content_type,
							filename,
							image.size[0],
							image.size[1],
							post_id.int(),
							user.user_id,
						),
						fetch_one=True,
					)
					updated: datetime = updated[0]
					image_size: PostSize = PostSize(
						width=image.size[0],
						height=image.size[1],
					)

				if old_filename :
					if not await self.b2_delete_file_async(f'{post_id}/{old_filename}') :
						self.logger.error(f'failed to delete old image: {post_id}/{old_filename}')

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

			post: Optional[InternalPost] = await self.kvs_get(post_id)
			if post :
				# post is populated in cache, so we can safely update it
				post.updated = updated
				post.media_type = MediaType(
					file_type=content_type[content_type.find('/')+1:],
					mime_type=content_type,
				)
				post.size = image_size
				post.filename = filename
				KVS.put(post_id, post)

			return {
				'post_id': post_id,
				'url': url,
				'emoji': emoji,
				'thumbnails': thumbnails,
			}

		finally :
			self.delete_file(file_on_disk)


	@HttpErrorHandler('updating post metadata')
	async def updatePostMetadata(self: 'Uploader', user: KhUser, post_id: PostId, title:str=None, description:str=None, privacy:Privacy=None, rating:Rating=None) -> Dict[str, Union[str, int, Dict[str, Union[None, str]]]]:
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
				params + [user.user_id, post_id.int()],
				fetch_one=True,
			)

			if privacy :
				await self._update_privacy(user, post_id.int(), privacy, transaction=t, commit=True)

			else :
				t.commit()

		post: Optional[InternalPost] = await self.kvs_get(post_id)
		if post :
			# post is populated in cache, so we can safely update it

			if privacy :
				post.privacy = privacy

			post = InternalPost.parse_obj({
				**post.dict(),
				**dict(zip(columns + ['created', 'updated'], params + list(data))),
			})

			KVS.put(post_id, post)

		return True


	async def _update_privacy(self: 'Uploader', user: KhUser, post_id: PostId, privacy: Privacy, transaction: Transaction = None, commit: bool = True) :
		if privacy == Privacy.unpublished :
			raise BadRequest('post privacy cannot be updated to unpublished.')

		with transaction or self.transaction() as t :
			data = t.query("""
				SELECT privacy.type
				FROM kheina.public.posts
					INNER JOIN kheina.public.privacy
						ON posts.privacy_id = privacy.privacy_id
				WHERE posts.uploader = %s
					AND posts.post_id = %s;
				""",
				(user.user_id, post_id.int()),
				fetch_one=True,
			)

			if not data :
				raise NotFound('the provided post does not exist or it does not belong to this account.')

			old_privacy: Privacy = Privacy[data[0]]

			if old_privacy == privacy :
				raise BadRequest('post privacy cannot be updated to the current privacy level.')

			if privacy == Privacy.draft and old_privacy != Privacy.unpublished :
				raise BadRequest('only unpublished posts can be marked as drafts.')

			tags_task: Task[TagGroups] = client.post_tags(post_id)

			if old_privacy in UnpublishedPrivacies and privacy not in UnpublishedPrivacies :
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
					user.user_id, post_id.int(), True,
					post_id.int(), 1, 0, 1, calc_hot(1, 0, time()), confidence(1, 1), calc_cont(1, 0),
					privacy.name, user.user_id, post_id.int(),
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
					privacy.name, user.user_id, post_id.int(),
				)

			t.query(query, params)

			try :
				tags: TagGroups = await tags_task

				if privacy == Privacy.public :
					ensure_future(self._increment_total_post_count(1))
					ensure_future(self._increment_user_count(user.user_id, 1))
					for tag in filter(None, flatten(tags)) :
						ensure_future(self._increment_tag_count(tag, 1))

				elif old_privacy == Privacy.public :
					ensure_future(self._increment_total_post_count(-1))
					ensure_future(self._increment_user_count(user.user_id, -1))
					for tag in filter(None, flatten(tags)) :
						ensure_future(self._increment_tag_count(tag, -1))

			except ClientResponseError as e :
				if e.status == 404 :
					return True

				raise

			if commit :
				t.commit()

		return True


	@HttpErrorHandler('updating post privacy')
	async def updatePrivacy(self: 'Uploader', user: KhUser, post_id: PostId, privacy: Privacy) :
		await self._update_privacy(user, post_id, privacy)

		if await KVS.exists_async(post_id) :
			# we need the created and updated values set by db, so just remove
			ensure_future(KVS.remove_async(post_id))


	@HttpErrorHandler('setting user icon')
	async def setIcon(self: 'Uploader', user: KhUser, post_id: PostId, coordinates: Coordinates) :
		if coordinates.width != coordinates.height :
			raise BadRequest(f'icons must be square. width({coordinates.width}) != height({coordinates.height})')

		ipost: Task[InternalPost] = ensure_future(client.post(post_id))
		iuser: Task[InternalUser] = ensure_future(client.user(user.user_id))
		image = None

		ipost: InternalPost = await ipost

		try :
			async with request(
				'GET',
				f'https://cdn.fuzz.ly/{post_id}/{quote(ipost.filename)}',
				raise_for_status=True,
			) as response :
				image = Image(blob=await response.read())

		except ClientResponseError as e :
			raise BadGateway('unable to retrieve image from B2.', inner_exception=str(e))

		# upload new icon
		image.crop(**coordinates.dict())
		self.convert_image(image, self.icon_size)

		iuser: InternalUser = await iuser
		handle = iuser.handle.lower()

		self.b2_upload(self.get_image_data(image), f'{post_id}/icons/{handle}.webp', self.mime_types['webp'])

		image.convert('jpeg')
		self.b2_upload(self.get_image_data(image), f'{post_id}/icons/{handle}.jpg', self.mime_types['jpeg'])

		image.close()

		# update db to point to new icon
		await self.query_async("""
			UPDATE kheina.public.users
				SET icon = %s
			WHERE users.user_id = %s;
			""",
			(post_id.int(), user.user_id),
			commit=True,
		)

		# cleanup old icons
		if post_id != iuser.icon :
			await self.b2_delete_file_async(f'{iuser.icon}/icons/{handle}.webp')
			await self.b2_delete_file_async(f'{iuser.icon}/icons/{handle}.jpg')

		iuser.icon = post_id
		ensure_future(UserKVS.put_async(str(iuser.user_id), iuser))


	@HttpErrorHandler('setting user banner')
	async def setBanner(self: 'Uploader', user: KhUser, post_id: PostId, coordinates: Coordinates) :
		if round(coordinates.width / 3) != coordinates.height :
			raise BadRequest(f'banners must be a 3x:1 rectangle. round(width / 3)({round(coordinates.width / 3)}) != height({coordinates.height})')

		ipost: Task[InternalPost] = ensure_future(client.post(post_id))
		iuser: Task[InternalUser] = ensure_future(client.user(user.user_id))
		image = None

		ipost: Post = await ipost

		try :
			async with request(
				'GET',
				f'https://cdn.fuzz.ly/{post_id}/{quote(ipost.filename)}',
				raise_for_status=True,
			) as response :
				image = Image(blob=await response.read())

		except ClientResponseError as e :
			raise BadGateway('unable to retrieve image from B2.', inner_exception=str(e))

		# upload new banner
		image.crop(**coordinates.dict())
		if image.size[0] > self.banner_size * 3 or image.size[1] > self.banner_size :
			image.resize(width=self.banner_size * 3, height=self.banner_size, filter=self.filter_function)

		iuser: InternalUser = await iuser
		handle = iuser.handle.lower()

		self.b2_upload(self.get_image_data(image), f'{post_id}/banners/{handle}.webp', self.mime_types['webp'])

		image.convert('jpeg')
		self.b2_upload(self.get_image_data(image), f'{post_id}/banners/{handle}.jpg', self.mime_types['jpeg'])

		image.close()

		# update db to point to new banner
		await self.query_async("""
			UPDATE kheina.public.users
				SET banner = %s
			WHERE users.user_id = %s;
			""",
			(post_id.int(), user.user_id),
			commit=True,
		)

		# cleanup old banners
		if post_id != iuser.banner :
			await self.b2_delete_file_async(f'{iuser.banner}/banners/{handle}.webp')
			await self.b2_delete_file_async(f'{iuser.banner}/banners/{handle}.jpg')

		iuser.banner = post_id
		ensure_future(UserKVS.put_async(str(iuser.user_id), iuser))
