import demo.api as api


def our_make(x):
	return x


def install():
	api.make = our_make
	api.make_all = our_make
