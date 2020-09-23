from kh_common.exceptions.http_error import BadRequest
from kh_common.exceptions import jsonErrorHandler
from starlette.responses import UJSONResponse
from kh_common.auth import retrieveTokenData
from traceback import format_tb
from kh_common import logging
from uploader import Uploader
import time


logger = logging.getLogger('upload-tool-server')
uploader = Uploader()


async def v1CreatePost(req) :
	"""
	only auth required
	"""
	try :
		token_data = retrieveTokenData(req)

		return UJSONResponse(
			uploader.createPost(token_data['user_id'])
		)

		else :
			raise BadRequest('no user id provided.')

	except :
		return jsonErrorHandler(req, logger)


async def v1UploadImage(req) :
	"""
	FORMDATA: {
		"post_id": str,
		"file": image file,
	}
	"""
	try :
		token_data = retrieveTokenData(req)
		requestFormdata = await req.form()
		
		file_obj = requestFormdata['file'].file
		filename = requestFormdata['file'].filename

		return UJSONResponse(
			uploader.uploadImageToPost(token_data['user_id'], file_obj, filename)
		)

	except :
		return jsonErrorHandler(req, logger)


async def v1UpdatePost(req) :
	"""
	{
		"post_id": str,
		"privacy": Optional[str],
		"title": Optional[str],
		"description": Optional[str]
	}
	"""
	try :
		token_data = retrieveTokenData(req)
		requestJson = await req.json()

		if 'post_id' in requestJson :
			return UJSONResponse(
				uploader.updatePostMetadata(token_data['user_id'], **requestJson)
			)

		else :
			raise BadRequest('no user id provided.')

	except :
		return jsonErrorHandler(req, logger)


async def v1Help(req) :
	return UJSONResponse({
		'/v1/create_post': {
			'auth': {
				'required': True,
				'user_id': 'int',
			},
		},
		'/v1/upload_image': {
			'auth': {
				'required': True,
				'user_id': 'int',
			},
			'file': 'image',
		},
		'/v1/update_post': {
			'auth': {
				'required': True,
				'user_id': 'int',
			},
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
