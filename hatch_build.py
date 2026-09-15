from hatchling.builders.hooks.plugin.interface import BuildHookInterface
class CustomHook(BuildHookInterface):
    def initialize(self, version, build_data):
        build_data['pure_python'] = False
        build_data['tag'] = self.getTag()

    def getTag(self):
        with open("platformName.txt") as f:
            tag = f.read().strip()
            tag = tag.replace(".", "_")
            tag = tag.replace("-", "_")
            tag = tag.replace("Linux", "linux")
            tag = "py3-none-"+tag
            return tag
