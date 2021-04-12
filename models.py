from pydantic import BaseModel
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


class PrivacyRequest(BaseModel) :
	post_id: str
	privacy: Privacy
