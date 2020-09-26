from pydantic import BaseModel
from enum import Enum, unique
from typing import Optional


@unique
class Privacy(Enum) :
	public: str = 'public'
	unlisted: str = 'unlisted'
	private: str = 'private'
	unpublished: str = 'unpublished'


class UpdateRequest(BaseModel) :
	post_id: str
	title: Optional[str]
	description: Optional[str]


class PrivacyRequest(BaseModel) :
	post_id: str
	privacy: Privacy
