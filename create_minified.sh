if ! [ -x "$(command -v zip)" ]; then
    echo 'Error: zip command not found-- this is necessary to minify' >&2
    exit 1
fi

if ! [ -d "$PWD/mini" ] || ! [ -d "$PWD/minilib" ]; then
    echo "Error: mini and minilib directories not found"
    exit 2
fi

temp=$(mktemp -d)
if ! [ -n "$temp" ]; then
    echo "Temporary folder not created? Exiting."
    exit 2
fi

cp -r "$PWD/mini" "$temp/mini"
cp -r "$PWD/minilib" "$temp/minilib"

if ! [ -x "$(command -v pyminify)" ]; then
    echo 'Warning: pyminify not found, which can shrink minitools by 30%. Continue? [y/n]' >&2
    read ans
    if [[ ! $ans == "y" ]]; then echo "Exiting.."; exit 2; fi
else
    echo "Compressing with pyminify..."
    pyminify "$temp/mini" --in-place --remove-literal-statements
    pyminify "$temp/minilib" --in-place --remove-literal-statements
fi
echo "Compressing to a single .zip..."
zip -9 -FSr minitools.zip "$temp/mini" "$temp/minilib" -x '*__pycache__*'
filesize=$(stat -c%s "minitools.zip")
rm -r "$temp/mini"
rm -r "$temp/minilib"
echo "Done! Written to $PWD/minitools.zip, compressed to $filesize bytes."
echo "If you are not sure how to use this .zip, check the README.md."