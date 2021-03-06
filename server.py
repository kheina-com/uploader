from kh_common.server import NoContentResponse, Request, ServerApp
from models import CreateRequest, IconRequest, PrivacyRequest, UpdateRequest
from fastapi.responses import UJSONResponse
from fastapi import File, Form, UploadFile
from uploader import Uploader
from typing import Optional


app = ServerApp()
uploader = Uploader()


@app.on_event('shutdown')
async def shutdown() :
	uploader.close()


@app.post('/v1/create_post')
def v1CreatePost(req: Request, body: CreateRequest) :
	"""
	only auth required
	"""

	if any(body.dict().values()) :
		return uploader.createPostWithFields(
			req.user,
			body.reply_to,
			body.title,
			body.description,
			body.privacy,
			body.rating,
		)

	return uploader.createPost(req.user.user_id)


@app.post('/v1/upload_image')
async def v1UploadImage(req: Request, file: UploadFile = File(None), post_id: Optional[str] = Form(None), web_resize: Optional[bool] = Form(None)) :
	"""
	FORMDATA: {
		"post_id": Optional[str],
		"file": image file,
		"web_resize": Optional[bool],
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
		req.user,
		file.file.read(),
		file.filename,
		post_id=post_id,
		web_resize=web_resize,
	)


@app.post('/v1/update_post')
def v1UpdatePost(req: Request, body: UpdateRequest) :
	"""
	{
		"post_id": str,
		"title": Optional[str],
		"description": Optional[str]
	}
	"""

	if uploader.updatePostMetadata(
		req.user,
		body.post_id,
		body.title,
		body.description,
		body.privacy,
		body.rating,
	) :
		return NoContentResponse


@app.post('/v1/update_privacy')
def v1UpdatePrivacy(req: Request, body: PrivacyRequest) :
	"""
	{
		"post_id": str,
		"privacy": str
	}
	"""

	if uploader.updatePrivacy(req.user.user_id, body.post_id, body.privacy) :
		return NoContentResponse


@app.post('/v1/set_icon')
async def v1SetIcon(req: Request, body: IconRequest) :
	await uploader.setIcon(req.user, body.post_id, body.coordinates)
	return NoContentResponse


@app.post('/v1/set_banner')
async def v1SetBanner(req: Request, body: IconRequest) :
	await uploader.setBanner(req.user, body.post_id, body.coordinates)
	return NoContentResponse


if __name__ == '__main__' :
	from uvicorn.main import run
	run(app, host='0.0.0.0', port=5001)
