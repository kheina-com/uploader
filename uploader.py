from kh_common.exceptions.http_error import BadRequest, InternalServerError
from kh_common.config.repo import name, short_hash
from typing import BinaryIO, Dict, List, Union
from kh_common.backblaze import B2Interface
from kh_common.logging import getLogger
from kh_common.sql import SqlInterface
from io import BytesIO
from math import floor
from uuid import uuid4
from PIL import Image


class Uploader(SqlInterface, B2Interface) :

	def __init__(self) -> None :
		SqlInterface.__init__(self)
		B2Interface.__init__(self, max_retries=100)
		self.logger = getLogger(f'{name}.{short_hash}')
		self.thumbnail_sizes: List[int] = [
			# the length of the longest side, in pixels
			100,
			200,
			400,
			800,
			1200,
		]
		self.resample_function: int = Image.BICUBIC


	def createPost(self, user_id: int) -> Dict[str, Union[str, int]] :
		try :
			data: List[str] = self.query("""
				SELECT kheina.public.create_new_post(%s);
				""",
				(user_id,),
				commit=True,
				fetch_one=True,
			)

		except :
			refid: str = uuid4().hex
			logdata: Dict[str, Union[str, int]] = {
				'refid': refid,
				'user_id': user_id,
			}
			self.logger.exception(logdata)
			raise InternalServerError('an error occurred while creating a new post.', logdata=logdata)

		return {
			'user_id': user_id,
			'post_id': data[0],
		}


	def uploadImage(self, user_id: int, file: BinaryIO, filename: str) -> Dict[str, Union[str, int, List[str]]] :
		# load the image first so we can verify it's not corrupt, etc
		image = Image.open(file)

		try :
			image.verify()
		
		except Exception as e :
			refid: str = uuid4().hex
			logdata = {
				'refid': refid,
				'user_id': user_id,
				'post_id': post_id,
				'filename': filename,
				'error': str(e),
			}
			self.logger.warning(logdata)
			raise BadRequest('user image failed validation.', logdata=logdata)

		content_type = self._get_mime_from_filename(image.format.lower())

		try :
			self.query("""
				CALL kheina.public.user_upload_file(%s, %s, %s);
				""",
				(
					user_id,
					content_type,
					filename,
				),
				commit=True,
			)

		except :
			refid: str = uuid4().hex
			logdata = {
				'refid': refid,
				'user_id': user_id,
				'post_id': post_id,
				'filename': filename,
			}
			self.logger.exception(logdata)
			raise InternalServerError('an error occurred while updating post metadata.', logdata=logdata)

		url = f'{post_id}/{filename}'

		try :
			logdata = {
				'user_id': user_id,
				'post_id': post_id,
				'url': url,
				'filename': filename,
				'image': 'full size',
				'color': image.mode,
				'type': image.format,
				'animated': image.is_animated,
			}

			# upload the raw file
			self.b2_upload(file_data, url, content_type=content_type)

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

				self.b2_upload(thumbnail_data.getvalue(), thumbnail_url)
				thumbnails[size] = thumbnail_url

		except :
			logdata['refid']: str = uuid4().hex
			self.logger.exception(logdata)
			raise InternalServerError('an error occurred while uploading an image to backblaze.', logdata=logdata)

		return {
			'user_id': user_id,
			'post_id': post_id,
			'url': url,
			'thumbnails': thumbnails,
		}


	def updatePostMetadata(self, user_id: int, post_id: str, privacy:str=None, title:str=None, description:str=None) -> Dict[str, Union[str, int, Dict[str, Union[None, str]]]]:
		query = """
			UPDATE kheina.public.posts
			SET updated_on = NOW()
			"""

		params = []
		return_data = { }

		if privacy :
			query += """,
			privacy_id = privacy_to_id(%s)"""
			params.append(privacy)
			return_data['privacy'] = None

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

		try :
			data = self.query(
				query + """
				WHERE post_id = %s and uploader = %s;
				RETURNING 
				""",
				params + [post_id, user_id],
				commit=True,
			)

		except :
			refid = uuid4().hex
			logdata = {
				'refid': refid,
				'user_id': user_id,
				'post_id': post_id,
				'privacy': privacy,
				'title': title,
				'description': description,
			}
			self.logger.exception(logdata)
			raise InternalServerError('an error occurred while updating post metadata.', logdata=logdata)

		return {
			'user_id': user_id,
			'post_id': post_id,
			'data': return_data,
		}
