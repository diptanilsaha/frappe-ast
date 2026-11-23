import frappe

type ConfType = dict[str, str]
type Pair[T] = tuple[T, T]


def first[T](values: list[T]) -> T:
	return values[0]


class Box[T: (int, str)](frappe._dict):
	def label(self) -> str:
		return f"{self.get("name")} ({self.get('kind')})"


def load(path):
	try:
		return open(path)
	except OSError, KeyError:
		return None


def report_url():
	return f"/api/method/demo.api.make?x={1}"
