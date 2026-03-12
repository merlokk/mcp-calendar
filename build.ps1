Remove-Item build -Recurse -Force -ErrorAction Ignore
mkdir build

docker run --rm `
  -v "${PWD}:/var/task" `
  --entrypoint /bin/sh `
  public.ecr.aws/lambda/python:3.13 `
  -c "python -m pip install --upgrade pip && pip install -r requirements-lambda.txt -t build"

Write-Output "copy..."

Copy-Item lambda_function.py build\
Copy-Item icscal build\icscal -Recurse

Write-Output "zipping..."

cd build
Compress-Archive -Path * -DestinationPath ..\lambda.zip -Force
cd ..
Write-Output "done"

