from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs("llama_cpp")
print(f"llama_cpp collected libraries: {binaries}")