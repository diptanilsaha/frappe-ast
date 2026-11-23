import demo.api as api
from demo.controllers.base_document import BaseDocument


def our_make(x):
	return x


def traced_archive(self):
	pass


class TaskOverride(BaseDocument):
	def validate(self):
		original = api.make
		api.make = our_make
		try:
			super().validate()
		finally:
			api.make = original


def install():
	original = api.get_count

	def counted(doctype):
		return original(doctype)

	api.get_count = counted
	setattr(BaseDocument, "archive", traced_archive)
	BaseDocument.archive_all = traced_archive  # its base (frappe Document) is not indexed: unknown
	api.daily = lambda: None
