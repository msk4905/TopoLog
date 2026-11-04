import sys
from pathlib import Path
import unittest
from html.parser import HTMLParser
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog.reporting import render, link

class Tags(HTMLParser):
    def __init__(self):super().__init__();self.tags=[];self.links=[]
    def handle_starttag(self,tag,attrs):
        self.tags.append(tag)
        if tag=='a':self.links.extend(v for k,v in attrs if k=='href')

class ReportTests(unittest.TestCase):
    def data(self):
        return {'verdict':'inconclusive','projects':[{'name':'<script>alert(1)</script>','file':'lab.gns3',
            'project_id':'project','status':'completed','verdict':'inconclusive','nodes':[{
            'name':'R1','node_id':'node','platform':'ios','collection':'collected',
            'commands':[],'checks':[{'status':'passed','criterion':{'kind':'ospf_neighbors','neighbors':['2.2.2.2']},
                                   'observed':{'2.2.2.2':'FULL/'},'evidence':'01.txt'}]}]}]}

    def test_structured_tables_and_local_evidence_links(self):
        text=render(self.data(),['report.md'])
        parser=Tags();parser.feed(text)
        self.assertIn('table',parser.tags)
        self.assertIn('details',parser.tags)
        self.assertIn('./project/node/01.txt',parser.links)
        self.assertIn('기대 조건',text)
        self.assertIn('관측값',text)
        self.assertNotIn('# TopoLog 일괄',text)
        self.assertIn('OSPF 이웃 FULL: 2.2.2.2',text)

    def test_names_and_observations_are_escaped_and_no_external_dependencies(self):
        data=self.data();data['projects'][0]['nodes'][0]['checks'][0]['observed']='<img src=x onerror=alert(1)>'
        text=render(data,[])
        self.assertNotIn('<script>',text)
        self.assertNotIn('<img src=x',text)
        self.assertIn('&lt;script&gt;',text)
        self.assertNotIn('src="https://',text)

    def test_unsafe_evidence_paths_are_not_clickable(self):
        for path in ('../secret','/etc/passwd','javascript:alert(1)','C:\\private.txt'):
            self.assertNotIn('<a ',link(path,'file'))

if __name__=='__main__':unittest.main()
