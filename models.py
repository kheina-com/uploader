from typing import List, Optional

from fuzzly_posts.models import PostId, PostIdValidator
from kh_common.models.privacy import Privacy
from kh_common.models.rating import Rating
from pydantic import BaseModel, validator


class PostSize(BaseModel) :
	width: int
	height: int


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


class Score(BaseModel) :
	up: int
	down: int
	total: int
	user_vote: Optional[int]


class MediaType(BaseModel) :
	file_type: str
	mime_type: str


class TagPortable(str) :
	pass


class TagGroups(BaseModel) :
	artist: Optional[List[TagPortable]]
	subject: Optional[List[TagPortable]]
	sponsor: Optional[List[TagPortable]]
	species: Optional[List[TagPortable]]
	gender: Optional[List[TagPortable]]
	misc: Optional[List[TagPortable]]
