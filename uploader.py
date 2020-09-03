from psycopg2.errors import UniqueViolation, ConnectionException
from psycopg2 import Binary, connect as dbConnect
from kh_common import getFullyQualifiedClassName
from kh_common.backblaze import B2Interface
from kh_common.logging import getLogger
from kh_common.sql import SqlInterface
from typing import List
from io import BytesIO
from math import floor
from PIL import Image


class Uploader(SqlInterface, B2Interface) :

	def __init__(self) :
		SqlInterface.__init__(self)
		B2Interface.__init__(self)
		self.logger = getLogger('upload-tool')
		self.thumbnail_sizes = [
			# the length of the longest side, in pixels
			100,
			200,
			400,
			800,
		]


	def createPost(self, uploader_user_id: int) :
		data = self.query("""
			INSERT INTO kheina.public.posts
			(uploader)
			VALUES
			(%s)
			RETURNING post_id;
			""",
			(uploader_user_id,),
			commit=True,
			fetch_one=True,
		)
		return {
			'post_id': data[0],
			'user_id': uploader_user_id,
		}


	def uploadImageToPost(self, post_id: str, user_id: int, file_data: bytes, filename: str) :
		content_type = self._get_mime_from_filename(filename)

		self.query("""
			UPDATE kheina.public.posts
			SET updated_on = NOW(),
				media_type_id = media_mime_type_to_id(%s),
				filename = %s
			WHERE post_id = %s and uploader = %s;
			""",
			(
				content_type,
				filename,
				post_id, user_id,
			),
			commit=True,
		)

		url = f'{post_id}/{filename}'

		# upload the raw file
		self.b2_upload(file_data, url, content_type=content_type)

		# render all thumbnails and queue them for upload async
		image = Image.open(BytesIO(file_data))
		image = image.convert('RGB')
		long_side = 0 if image.size[0] > image.size[1] else 1

		thumbnails = {}

		thumbnail_data = None
		max_size = False
		for size in self.thumbnail_sizes :
			ratio = size / image.size[long_side]
			if ratio < 1 :
				# resize and output
				thumbnail_data = BytesIO()
				output_size = (floor(image.size[0] * ratio), size) if long_side else (size, floor(image.size[1] * ratio))
				thumbnail = image.resize(output_size, resample=Image.BICUBIC).save(thumbnail_data, format='JPEG', quality=60)

			elif not thumbnail_data or not max_size :
				# just convert what we have
				thumbnail_data = BytesIO()
				thumbnail = image.save(thumbnail_data, format='JPEG', quality=60)
				max_size = True

			thumbnail_url = f'{post_id}/thumbnails/{size}.jpg'
			self.b2_upload(thumbnail_data.getvalue(), thumbnail_url)
			thumbnails[size] = thumbnail_url
		
		return {
			'post_id': post_id,
			'url': url,
			'thumbnails': thumbnails,
		}


	def updatePostMetadata(self, post_id: str, user_id: int, privacy:str=None, title:str=None, description:str=None) :
		query = """
			UPDATE kheina.public.posts
			SET updated_on = NOW()
			"""

		params = []

		if privacy :
			query += """,
			privacy_id = privacy_to_id(%s)"""
			params.append(privacy)

		if title :
			query += """,
			title = %s"""
			params.append(title)

		if description :
			query += """,
			description = %s"""
			params.append(description)

		if params :
			data = self.query(
				query + "WHERE post_id = %s and uploader = %s;",
				params + [post_id, user_id],
				commit=True,
			)

		return {
			'success': True,
		}
