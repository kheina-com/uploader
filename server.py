from models import CreateRequest, PrivacyRequest, UpdateRequest
from fastapi.responses import Response, NoContentResponse
from kh_common.server import Request, ServerApp
from fastapi import File, Form, UploadFile
from uploader import Uploader
from typing import Optional


app = ServerApp()
uploader = Uploader()


@app.on_event('shutdown')
async def shutdown() :
	uploader.close()


@app.post('/v1/create_post')
async def v1CreatePost(req: Request, body: CreateRequest) :
	"""
	only auth required
	"""

	if any(body.dict().values()) :
		return UJSONResponse(
			uploader.createPostWithFields(
				req.user,
				body.reply_to,
				body.title,
				body.description,
				body.privacy,
				body.rating,
			)
		)

	return uploader.createPost(req.user.user_id)


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
					'loc': [
						'body',
						'file'
					],
					'msg': 'field required',
					'type': 'value_error.missing',
				},
			]
		}, status_code=422)

	return await uploader.uploadImage(
		req.user.user_id,
		file.file.read(),
		file.filename,
		post_id=post_id,
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

	if uploader.updatePostMetadata(
		req.user.user_id,
		body.post_id,
		body.title,
		body.description,
		body.privacy,
		body.rating,
	) :
		return NoContentResponse


@app.post('/v1/update_privacy')
async def v1UpdatePrivacy(req: Request, body: PrivacyRequest) :
	"""
	{
		"post_id": str,
		"privacy": str
	}
	"""

	if uploader.updatePrivacy(req.user.user_id, body.post_id, body.privacy) :
		return NoContentResponse


if __name__ == '__main__' :
	from uvicorn.main import run
	run(app, host='0.0.0.0', port=5001)
