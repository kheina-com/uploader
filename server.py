from kh_common.exceptions import asyncJsonErrorHandler
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

		if user_id in requestJson :
			return UJSONResponse(
				Uploader.createPost(requestJson['user_id'])
			)

		else :
			return UJSONResponse({
				'error': 'no user id provided.',
			})

	except :
		return await asyncJsonErrorHandler(req)


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

		return UJSONResponse(
			uploader.uploadImageToPost(**requestFormdata)
		)

	except :
		return await asyncJsonErrorHandler(req)


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

		if post_id in requestJson and user_id in requestJson :
			return UJSONResponse(
				Uploader.updatePostMetadata(**requestJson)
			)

		else :
			return UJSONResponse({
				'error': 'no user id provided.',
			})

	except :
		return await asyncJsonErrorHandler(req)


async def v1help(req) :
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
]

app = Starlette(
	routes=routes,
	middleware=middleware,
	on_shutdown=[shutdown],
)

if __name__ == '__main__' :
	from uvicorn.main import run
	run(app, host='127.0.0.1', port=5000)
