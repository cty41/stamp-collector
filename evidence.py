"""Import explicit review evidence from JSON. Does not identify or value stamps."""
import argparse
from collection import Store, jsonfile

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True)
    p.add_argument('file',help='JSON array of evidence records; approved means human-reviewed')
    a=p.parse_args();s=Store(a.root);s.init()
    for e in jsonfile(a.file):print(s.add_attention(e))

if __name__=='__main__':main()
