from kh_common.exceptions.http_error import BadRequest
from kh_common.auth import authenticated, TokenData
from kh_common.exceptions import jsonErrorHandler
from models import PrivacyRequest, UpdateRequest
from kh_common.validation import validatedJson
from starlette.responses import UJSONResponse
from kh_common.logging import getLogger
from starlette.requests import Request
from traceback import format_tb
from uploader import Uploader
import time


logger = getLogger()
uploader = Uploader()


@jsonErrorHandler
@authenticated
async def v1CreatePost(req: Request, token_data:TokenData=None) :
	"""
	only auth required
	"""

	return UJSONResponse(
		uploader.createPost(token_data.data['user_id'])
	)


@jsonErrorHandler
@authenticated
async def v1UploadImage(req: Request, token_data:TokenData=None) :
	"""
	FORMDATA: {
		"post_id": Optional[str],
		"file": image file,
	}
	"""
	requestFormdata = await req.form()

	if 'file' not in requestFormdata :
		raise BadRequest('no file provided.')

	file_data = requestFormdata['file'].file
	filename = requestFormdata['file'].filename
	post_id = requestFormdata.get('post_id')

	return UJSONResponse(
		await uploader.uploadImage(token_data.data['user_id'], file_data.read(), filename, post_id=post_id)
	)


@jsonErrorHandler
@authenticated
@validatedJson
async def v1UpdatePost(req: UpdateRequest, token_data:TokenData=None) :
	"""
	{
		"post_id": str,
		"title": Optional[str],
		"description": Optional[str]
	}
	"""

	return UJSONResponse(
		uploader.updatePostMetadata(token_data.data['user_id'], req.post_id, req.title, req.description)
	)


@jsonErrorHandler
@authenticated
@validatedJson
async def v1UpdatePrivacy(req: PrivacyRequest, token_data:TokenData=None) :
	"""
	{
		"post_id": str,
		"privacy": str
	}
	"""

	return UJSONResponse(
		uploader.updatePrivacy(token_data.data['user_id'], req.post_id, req.privacy)
	)


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
			'post_id': 'Optional[str]',
		},
		'/v1/update_post': {
			'auth': {
				'required': True,
				'user_id': 'int',
			},
			'title': 'Optional[str]',
			'description': 'Optional[str]',
		},
		'/v1/update_privacy': {
			'auth': {
				'required': True,
				'user_id': 'int',
			},
			'privacy': 'str',
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
	Middleware(TrustedHostMiddleware, allowed_hosts={ 'localhost', '127.0.0.1', 'upload.kheina.com', 'upload-dev.kheina.com' }),
]

routes = [
	Route('/v1/create_post', endpoint=v1CreatePost, methods=('POST',)),
	Route('/v1/upload_image', endpoint=v1UploadImage, methods=('POST',)),
	Route('/v1/update_post', endpoint=v1UpdatePost, methods=('POST',)),
	Route('/v1/update_privacy', endpoint=v1UpdatePrivacy, methods=('POST',)),
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
