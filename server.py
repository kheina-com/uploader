from starlette.middleware.trustedhost import TrustedHostMiddleware
from kh_common.exceptions.http_error import UnprocessableEntity
from fastapi import FastAPI, File, Form, Request, UploadFile
from kh_common.exceptions import jsonErrorHandler
from models import PrivacyRequest, UpdateRequest
from starlette.responses import UJSONResponse
from kh_common.auth import KhAuthMiddleware
from uploader import Uploader
from typing import Optional


app = FastAPI()
app.add_exception_handler(Exception, jsonErrorHandler)
app.add_middleware(TrustedHostMiddleware, allowed_hosts={ 'localhost', '127.0.0.1', 'upload.kheina.com', 'upload-dev.kheina.com' })
app.add_middleware(KhAuthMiddleware)

uploader = Uploader()


@app.on_event('shutdown')
async def shutdown() :
	uploader.close()


@app.post('/v1/create_post')
async def v1CreatePost(req: Request) :
	"""
	only auth required
	"""

	return UJSONResponse(
		uploader.createPost(req.user.user_id)
	)


@app.post('/v1/upload_image')
async def v1UploadImage(req: Request, file: UploadFile = File(None), post_id: Optional[str] = Form(None)) :
	"""
	FORMDATA: {
		"post_id": Optional[str],
		"file": image file,
	}
	"""

	if not file :
		# since it doesn't do this for us, send the proper error back
		return UJSONResponse({
			'detail': [
				{
					'loc':['body', 'file'],
					'msg': 'field required',
					'type': 'value_error.missing'
				},
			]
		}, status_code=422)

	return UJSONResponse(
		await uploader.uploadImage(
			req.user.user_id,
			file.file.read(),
			file.filename,
			post_id=post_id,
		)
	)


@app.post('/v1/update_post')
async def v1UpdatePost(req: Request, body: UpdateRequest) :
	"""
	{
		"post_id": str,
		"title": Optional[str],
		"description": Optional[str]
	}
	"""

	return UJSONResponse(
		uploader.updatePostMetadata(
			req.user.user_id,
			body.post_id,
			body.title,
			body.description,
		)
	)


@app.post('/v1/update_privacy')
async def v1UpdatePrivacy(req: Request, body: PrivacyRequest) :
	"""
	{
		"post_id": str,
		"privacy": str
	}
	"""

	return UJSONResponse(
		uploader.updatePrivacy(req.user.user_id, body.post_id, body.privacy)
	)


if __name__ == '__main__' :
	from uvicorn.main import run
	run(app, host='127.0.0.1', port=5001)
