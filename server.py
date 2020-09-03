from kh_common.exceptions.http_error import BadRequest
from kh_common.exceptions import jsonErrorHandler
from starlette.responses import UJSONResponse
from traceback import format_tb
from kh_common import logging
from uploader import Uploader
import time


logger = logging.getLogger('upload-tool-server')
uploader = Uploader()


async def v1CreatePost(req) :
	"""
	{
		"version": Optional[str],
		"algorithm": Optional[str],
		"key_id": int
	}
	"""
	try :
		requestJson = await req.json()

		if 'user_id' in requestJson :
			return UJSONResponse(
				uploader.createPost(requestJson['user_id'])
			)

		else :
			raise BadRequest('no user id provided.')

	except :
		return jsonErrorHandler(req, logger)


async def v1UploadImage(req) :
	"""
	FORMDATA: {
		"post_id": str,
		"user_id": int,
		"file_data": bytes,
		"filename": str
	}
	"""
	try :
		requestFormdata = await req.form()
		
		file_data = requestFormdata['file_data'].file.read()
		post_id = requestFormdata['post_id']
		user_id = requestFormdata['user_id']
		filename = requestFormdata['filename']

		return UJSONResponse(
			uploader.uploadImageToPost(post_id, user_id, file_data, filename)
		)

	except :
		return jsonErrorHandler(req, logger)


async def v1UpdatePost(req) :
	"""
	{
		"post_id": str,
		"user_id": int,
		"privacy": Optional[str],
		"title": Optional[str],
		"description": Optional[str]
	}
	"""
	try :
		requestJson = await req.json()

		if 'post_id' in requestJson and 'user_id' in requestJson :
			return UJSONResponse(
				uploader.updatePostMetadata(**requestJson)
			)

		else :
			raise BadRequest('no user id provided.')

	except :
		return jsonErrorHandler(req, logger)


async def v1Help(req) :
	return UJSONResponse({
		'/v1/create_post': {
			'user_id': 'int',
		},
		'/v1/upload_image': {
			'post_id': 'str',
			'user_id': 'int',
			'file_data': 'bytes',
			'filename': 'str',
		},
		'/v1/update_post': {
			'post_id': 'str',
			'user_id': 'int',
			'privacy': 'Optional[str]',
			'title': 'Optional[str]',
			'description': 'Optional[str]',
		},
	})


async def shutdown() :
	uploader.close()


from starlette.applications import Starlette
from starlette.staticfiles import StaticFiles
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Route, Mount

middleware = [
	# Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts),
]

routes = [
	Route('/v1/create_post', endpoint=v1CreatePost, methods=('POST',)),
	Route('/v1/upload_image', endpoint=v1UploadImage, methods=('POST',)),
	Route('/v1/update_post', endpoint=v1UpdatePost, methods=('POST',)),
	Route('/v1/help', endpoint=v1Help, methods=('GET',)),
]

app = Starlette(
	routes=routes,
	middleware=middleware,
	on_shutdown=[shutdown],
)

if __name__ == '__main__' :
	from uvicorn.main import run
	run(app, host='127.0.0.1', port=5001)
