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
sudo python3 -m pip install -r requirements.txt
curl https://dl.google.com/go/go1.21.7.linux-amd64.tar.gz -o golang.tar.gz
sudo tar -C /usr/local -xf golang.tar.gz
export PATH=$PATH:/usr/local/go/bin
export GOPATH=$HOME/go
export PATH=$PATH:$GOPATH/bin
go install github.com/kheina-com/go-thumbhash/cmd/thumbhash@1efbb9d
```

## requires
https://exiftool.org/install.html
https://wiki.python.org/moin/ImageMagick