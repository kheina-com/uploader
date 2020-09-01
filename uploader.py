from psycopg2.errors import UniqueViolation, ConnectionException
from psycopg2 import Binary, connect as dbConnect
from kh_common import getFullyQualifiedClassName
from kh_common.backblaze import B2Interface
from kh_common.logging import getLogger
from kh_common.sql import SqlInterface
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


	def createPost(self, uploader_user_id) :
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


	def uploadImageToPost(self, file_data, filename, post_id) :
		# upload the raw file
		self.b2_upload(file_data, f'{post_id}/{filename}')

		# render all thumbnails and queue them for upload async
		image = Image(BytesIO(file_data))
		image = image.convert('RGB')
		long_side = 0 if image.size[0] > image.size[1] else 1

		thumbnail_data = None
		for size in self.thumbnail_sizes :
			ratio = size / image.size[long_side]
			if ratio < 1 :
				# resize and output
				thumbnail_data = BytesIO()
				output_size = (floor(image.size[0]) * ratio, size) if long_side else (size, floor(image.size[1]) * ratio)
				thumbnail = image.resize(output_size, resample=Image.BICUBIC).save(thumbnail_data, format='JPEG', quality=60)

			elif not thumbnail_data :
				# just convert what we have
				thumbnail_data = BytesIO()
				thumbnail = image.save(thumbnail_data, format='JPEG', quality=60)

			self.b2_upload(thumbnail_data, f'{post_id}/thumbnails/{size}.jpg')


	def updatePostMetadata(self, metadata) :
		pass
