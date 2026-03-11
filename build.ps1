Remove-Item build -Recurse -Force -ErrorAction Ignore
mkdir build

pip install `
  --platform manylinux2014_x86_64 `
  --implementation cp `
  --python-version 3.13 `
  --only-binary=:all: `
  --target build `
  -r requirements.txt

copy lambda_function.py build\

cd build
Compress-Archive -Path * -DestinationPath ..\lambda.zip -Force
cd ..
