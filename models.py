from datetime import datetime
from typing import List, Optional

from kh_common.models.privacy import Privacy
from kh_common.models.rating import Rating
from kh_common.models.user import UserPortable
from pydantic import BaseModel


class PostSize(BaseModel) :
	width: int
	height: int


class UpdateRequest(BaseModel) :
	post_id: str
	title: Optional[str]
	description: Optional[str]
	rating: Optional[Rating]
	privacy: Optional[Privacy]


class CreateRequest(BaseModel) :
	reply_to: Optional[str]
	title: Optional[str]
	description: Optional[str]
	rating: Optional[Rating]
	privacy: Optional[Privacy]


class PrivacyRequest(BaseModel) :
	post_id: str
	privacy: Privacy


class Coordinates(BaseModel) :
	top: int
	left: int
	width: int
	height: int


class IconRequest(BaseModel) :
	post_id: str
	coordinates: Coordinates


class Score(BaseModel) :
	up: int
	down: int
	total: int
	user_vote: Optional[int]


class MediaType(BaseModel) :
	file_type: str
	mime_type: str


class Post(BaseModel) :
	post_id: str
	user_id: int
	title: Optional[str]
	description: Optional[str]
	user: str
	score: Optional[Score]
	rating: Rating
	parent: Optional[str]
	privacy: Privacy
	created: Optional[datetime]
	updated: Optional[datetime]
	filename: Optional[str]
	media_type: Optional[MediaType]


class TagPortable(str) :
	pass


class TagGroups(BaseModel) :
	artist: Optional[List[TagPortable]]
	subject: Optional[List[TagPortable]]
	sponsor: Optional[List[TagPortable]]
	species: Optional[List[TagPortable]]
	gender: Optional[List[TagPortable]]
	misc: Optional[List[TagPortable]]
