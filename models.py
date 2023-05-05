from typing import Optional

from fuzzly.models.post import PostId, PostIdValidator, Privacy, Rating
from pydantic import BaseModel, validator


class UpdateRequest(BaseModel) :
	_post_id_validator = PostIdValidator

	post_id: PostId
	title: Optional[str]
	description: Optional[str]
	rating: Optional[Rating]
	privacy: Optional[Privacy]


class CreateRequest(BaseModel) :
	reply_to: Optional[PostId]
	title: Optional[str]
	description: Optional[str]
	rating: Optional[Rating]
	privacy: Optional[Privacy]

	@validator('reply_to', pre=True, always=True)
	def _parent_validator(value) :
		if value :
			return PostId(value)


class PrivacyRequest(BaseModel) :
	_post_id_validator = PostIdValidator

	post_id: PostId
	privacy: Privacy


class Coordinates(BaseModel) :
	top: int
	left: int
	width: int
	height: int


class IconRequest(BaseModel) :
	_post_id_validator = PostIdValidator

	post_id: PostId
	coordinates: Coordinates


class TagPortable(str) :
	pass
