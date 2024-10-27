if ! [ -x "$(command -v zip)" ]; then
    echo 'Error: zip command not found-- this is necessary to minify' >&2
    exit 1
fi

if ! [ -d "$PWD/mini" ] || ! [ -d "$PWD/minilib" ]; then
    echo "Error: mini and minilib directories not found" >&2
    exit 2
fi

temp=$(mktemp -d)
if ! [ -n "$temp" ]; then
    echo "Temporary folder not created? Exiting." >&2
    exit 2
fi

cp -r "$PWD/mini" "$temp/mini"
cp -r "$PWD/minilib" "$temp/minilib"

if ! [ -x "$(command -v pyminify)" ] || [ "$1" == "no-minify" ]; then
    echo 'Warning: pyminify not found, which can significantly compress minitools. Continue? [y/n]' >&2
    echo "(pyminify can be installed with 'pip install python-minifier')"
    read ans
    if [[ ! $ans == "y" ]]; then echo "Exiting.."; exit 2; fi
else
    echo "Compressing with pyminify..."
    pyminify "$temp/mini" --in-place --remove-literal-statements > /dev/null
    pyminify "$temp/minilib" --in-place --remove-literal-statements > /dev/null
fi
echo "Compressing to a single .zip..."
(cd "$temp" && zip -9 -FSrq - "./mini" "./minilib" -x '*__pycache__*') > minitools.zip
filesize=$(stat -c%s "minitools.zip")
(cd "$temp" && rm -r "./mini" && rm -r "./minilib") # WSL doesn't clear /tmp, mostly for me
echo "Done! Written to ./minitools.zip, compressed to $filesize bytes."
echo "If you are not sure how to use this .zip, check the README.md."