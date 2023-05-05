from typing import Dict, List, Optional, Union

from fastapi import File, Form, UploadFile
from fastapi.responses import UJSONResponse
from fuzzly.models.post import PostId
from kh_common.server import NoContentResponse, Request, ServerApp

from models import CreateRequest, IconRequest, PrivacyRequest, UpdateRequest
from uploader import Uploader


app = ServerApp(
	auth_required = False,
	allowed_hosts = [
		'localhost',
		'127.0.0.1',
		'*.kheina.com',
		'kheina.com',
		'*.fuzz.ly',
		'fuzz.ly',
	],
	allowed_origins = [
		'localhost',
		'127.0.0.1',
		'dev.kheina.com',
		'kheina.com',
		'dev.fuzz.ly',
		'fuzz.ly',
	],
)
uploader = Uploader()


@app.on_event('shutdown')
async def shutdown() :
	uploader.close()


@app.post('/v1/create_post')
async def v1CreatePost(req: Request, body: CreateRequest) :
	"""
	only auth required
	"""
	await req.user.authenticated()

	if any(body.dict().values()) :
		return await uploader.createPostWithFields(
			req.user,
			body.reply_to,
			body.title,
			body.description,
			body.privacy,
			body.rating,
		)

	return await uploader.createPost(req.user)


@app.post('/v1/upload_image')
async def v1UploadImage(req: Request, file: UploadFile = File(None), post_id: PostId = Form(None), web_resize: Optional[int] = Form(None)) :
	"""
	FORMDATA: {
		"post_id": Optional[str],
		"file": image file,
		"web_resize": Optional[bool],
	}
	"""
	await req.user.authenticated()

	# since it doesn't do this for us, send the proper error back
	detail: List[Dict[str, Union[str, List[str]]]] = []

	if not file :
		detail.append({
			'loc': [
				'body',
				'file'
			],
			'msg': 'field required',
			'type': 'value_error.missing',
		})

	if not post_id :
		detail.append({
			'loc': [
				'body',
				'post_id'
			],
			'msg': 'field required',
			'type': 'value_error.missing',
		})

	if detail :
		return UJSONResponse({ 'detail': detail }, status_code=422)

	return await uploader.uploadImage(
		user=req.user,
		file_data=file.file.read(),
		filename=file.filename,
		post_id=PostId(post_id),
		web_resize=web_resize,
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
	await req.user.authenticated()

	if await uploader.updatePostMetadata(
		req.user,
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
	await req.user.authenticated()

	if await uploader.updatePrivacy(req.user, body.post_id, body.privacy) :
		return NoContentResponse


@app.post('/v1/set_icon')
async def v1SetIcon(req: Request, body: IconRequest) :
	await req.user.authenticated()
	await uploader.setIcon(req.user, body.post_id, body.coordinates)
	return NoContentResponse


@app.post('/v1/set_banner')
async def v1SetBanner(req: Request, body: IconRequest) :
	await req.user.authenticated()
	await uploader.setBanner(req.user, body.post_id, body.coordinates)
	return NoContentResponse


if __name__ == '__main__' :
	from uvicorn.main import run
	run(app, host='0.0.0.0', port=5001)
