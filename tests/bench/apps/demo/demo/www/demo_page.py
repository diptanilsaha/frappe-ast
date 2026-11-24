from demo.dispatch import use_engine


def get_context(context):
	context.result = use_engine()
