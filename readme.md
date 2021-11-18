## installation
```
sudo yum install perl-devel
curl https://exiftool.org/Image-ExifTool-12.36.tar.gz -o Image-ExifTool-12.36.tar.gz
gzip -dc Image-ExifTool-12.36.tar.gz | tar -xf -
cd Image-ExifTool-12.36/
perl Makefile.PL
make test
sudo make install
sudo yum install ImageMagick-devel
sudo pip3 install -r requirements.txt
```

## requires
https://exiftool.org/install.html
https://wiki.python.org/moin/ImageMagick