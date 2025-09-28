import sys, json
from jinja2 import Environment, FileSystemLoader

def main(tpl_path, data_path):
    tpl_dir = str(__import__("pathlib").Path(tpl_path).parent)
    tpl_name = __import__("pathlib").Path(tpl_path).name
    env = Environment(loader=FileSystemLoader(tpl_dir), autoescape=False, trim_blocks=True, lstrip_blocks=True)
    data = json.load(open(data_path, "r", encoding="utf-8"))
    print(env.get_template(tpl_name).render(**data))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
