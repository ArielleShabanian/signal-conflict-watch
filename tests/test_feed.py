import datetime as dt
import email.utils
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1]/'scripts'/'update_feed.py'
spec=importlib.util.spec_from_file_location('signal_feed',MODULE)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FeedTests(unittest.TestCase):
    def raw(self,title,source='Reuters'):
        now=email.utils.format_datetime(mod.NOW-dt.timedelta(minutes=20))
        return ('<rss><channel><item><title>'+title+'</title>'
                '<link>https://example.com/story</link><pubDate>'+now+'</pubDate>'
                '<source>'+source+'</source></item></channel></rss>').encode()
    def test_accepted_military_article(self):
        articles=mod.parse_rss(self.raw('Iran missile attack reported'), 'military')
        self.assertEqual(len(articles),1)
        self.assertEqual(articles[0]['status'],'reported')
    def test_rhetoric_requires_explicit_keyword(self):
        self.assertEqual(mod.parse_rss(self.raw('Iran missile incident'), 'rhetoric'),[])
        self.assertEqual(len(mod.parse_rss(self.raw('Iran Mahdi prophecy discussed'), 'rhetoric')),1)
    def test_unknown_publisher_excluded(self):
        self.assertEqual(mod.parse_rss(self.raw('Iran missile attack','Some Blog'),'military'),[])
    def test_indicator_ceiling_without_fact_check(self):
        headlines=[{'category':'military','publisher':f'Publisher{i%8}', 'title':'Iran struck military installation', 'published_at':mod.NOW.isoformat()} for i in range(40)]
        headlines += [{'category':'civilian','publisher':f'Publisher{i%8}', 'title':'Iranian civilians killed in strike', 'published_at':mod.NOW.isoformat()} for i in range(10)]
        v,_=mod.score_signal(headlines)
        self.assertEqual(v,8)

if __name__ == '__main__':unittest.main()
