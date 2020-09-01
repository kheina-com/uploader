from psycopg2.errors import UniqueViolation, ConnectionException
from psycopg2 import Binary, connect as dbConnect
from kh_common import getFullyQualifiedClassName
from kh_common.backblaze import B2Interface
from kh_common.logging import getLogger
from kh_common.sql import SqlInterface


class Uploader(SqlInterface, B2Interface) :

	def __init__(self) :
		SqlInterface.__init__(self)
		B2Interface.__init__(self)
		self.logger = getLogger('upload-tool')
		self.b2_authorize()


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


	def uploadFileToPost(self, file_data, filename, post_id) :
		filename = f'{post_id}/{filename}'
		self.b2_upload(file_data, filename)

		# render all thumbnails and queue them for upload async


	def updatePostMetadata(self, metadata) :
		pass
