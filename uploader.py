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
		self.authorize_b2()


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
			'post_id': post_id,
			'user_id': uploader_user_id,
		}


	def uploadFileToPost(self, file_data, post_id) :
		filename = f'{post_id}/heck.png'
		self.b2_upload(file_data, 'image/png', filename)


	def updatePostMetadata(self, metadata) :
		pass
