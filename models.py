from kh_common.models.user import UserPortable
from kh_common.models.rating import Rating
from pydantic import BaseModel
from datetime import datetime
from enum import Enum, unique
from typing import Optional


@unique
class Privacy(Enum) :
	public: str = 'public'
	unlisted: str = 'unlisted'
	private: str = 'private'


@unique
class Rating(Enum) :
	general: str = 'general'
	mature: str = 'mature'
	explicit: str = 'explicit'


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
	title: Optional[str]
	description: Optional[str]
	user: UserPortable
	score: Optional[Score]
	rating: Rating
	parent: Optional[str]
	privacy: Privacy
	created: Optional[datetime]
	updated: Optional[datetime]
	filename: Optional[str]
	media_type: Optional[MediaType]
	blocked: bool
