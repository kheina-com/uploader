from pydantic import BaseModel
from enum import Enum, unique
from typing import Optional


@unique
class Privacy(Enum) :
	public: int = 1
	unlisted: int = 2
	private: int = 3
	unpublished: int = 4


class UpdateRequest(BaseModel) :
	post_id: str
	title: Optional[str]
	description: Optional[str]


class PrivacyRequest(BaseModel) :
	post_id: str
	privacy: Privacy
