import json

import bs4

from bs4 import BeautifulSoup

html_file = "./songlist_output.html"

def html_analysis(html_content):
    '''
    分析HTML内容，提取歌曲列表
    '''

    # 具体提取内容：
    # <tr><td><a href="/wiki/url" title="songname">songname</a></td><td>Artist</td><td style="text-align:center">5</td><td style="text-align:center">7</td><td style="text-align:center">12</td></tr>
    # 之中的url和songname

    # 提取所有歌曲列表
    soup = BeautifulSoup(html_content, 'html.parser')
    song_list = []
    for row in soup.find_all('tr'):
        if row.find('a'):
            url = row.find('a')['href']
            songname = row.find('a').text.strip()
            song_list.append((url, songname))
    return song_list

def write_file(songlist):
    '''
    写入歌曲列表到JSON文件

    格式：
        [
            {
                "url": "url",
                "songname": "songname"
            },
            ...
        ]
    '''
    with open('songlist.json', 'w', encoding='utf-8') as f:
        json.dump(songlist, f, ensure_ascii=False, indent=4)

def main():
    html_content = open(html_file, 'r', encoding='utf-8').read()
    song_list = html_analysis(html_content)
    print(song_list)
    write_file(song_list)

if __name__ == "__main__":
    main()