"""Bounded configuration-to-operational checks; no fabricated traffic policy."""
import ipaddress
import re


def derive(config):
    result=[];interface=None;pool=None
    for raw in config.splitlines():
        line=raw.strip()
        if not line:continue
        if not raw[0].isspace():
            interface=line.split(None,1)[1] if line.startswith('interface ') else None
            pool=line[len('ip dhcp pool '):] if line.startswith('ip dhcp pool ') else None
        if pool:
            m=re.fullmatch(r'network (\S+) (\S+)',line)
            if m:
                try:network=str(ipaddress.IPv4Network(m[1]+'/'+m[2],strict=False))
                except ValueError:continue
                result.append(('dhcp',{'kind':'dhcp_pool','command':'show ip dhcp pool','pool':pool,'network':network}))
        if interface:
            m=re.fullmatch(r'ip access-group (\S+) (in|out)',line)
            if m:result.append(('acl',{'kind':'acl_binding','command':'show ip interface','interface':interface,'acl':m[1],'direction':m[2]}))
        m=re.fullmatch(r'ip nat inside source static (\S+) (\S+)',line)
        if m:
            try:local,global_ip=[str(ipaddress.IPv4Address(x)) for x in m.groups()]
            except ValueError:continue
            result.append(('nat',{'kind':'static_nat','command':'show ip nat translations','local':local,'global':global_ip}))
    return result[:128]


def evaluate(rule,text):
    kind=rule['kind']
    if kind=='dhcp_pool':
        block=re.search(r'(?ims)^Pool\s+'+re.escape(rule['pool'])+r'\s*:\s*(.*?)(?=^Pool\s|\Z)',text)
        if not block:return False,'지정한 DHCP 풀이 없습니다.'
        network=ipaddress.IPv4Network(rule['network']);ranges=re.findall(r'(\d+\.\d+\.\d+\.\d+)\s*-\s*(\d+\.\d+\.\d+\.\d+)',block[1])
        if not ranges:return None,'DHCP 풀은 있으나 주소 범위를 해석하지 못했습니다.'
        if network.prefixlen>=31:return None,'/31·/32 DHCP 주소 범위는 자동 판정하지 않습니다.'
        try:valid=any(ipaddress.IPv4Address(a)==network.network_address+1 and ipaddress.IPv4Address(b)==network.broadcast_address-1 for a,b in ranges)
        except ValueError:return None,'DHCP 주소 범위를 IPv4로 해석하지 못했습니다.'
        return valid, ranges
    if kind=='static_nat':
        rows=[line.split() for line in text.splitlines()]
        return any(len(row)>2 and row[1]==rule['global'] and row[2]==rule['local'] for row in rows),'기대 global/local: '+rule['global']+' / '+rule['local']
    if kind=='acl_binding':
        block=re.search(r'(?ims)^'+re.escape(rule['interface'])+r' is .*?(?=^\S+ is |\Z)',text)
        if not block:return None,'인터페이스 상세 증거가 없습니다.'
        label='Inbound' if rule['direction']=='in' else 'Outgoing'
        acl=re.search(label+r' access list is (\S+)',block[0],re.I)
        return (acl[1]==rule['acl'] if acl else None),acl[1] if acl else 'ACL 적용 위치를 해석하지 못했습니다.'
    if kind=='stp_instance':
        block=re.search(r'(?ims)^VLAN0*'+str(rule['vlan'])+r'\s*(.*?)(?=^VLAN\d|\Z)',text)
        return bool(block and 'Root ID' in block[1] and 'Bridge ID' in block[1]),'VLAN '+str(rule['vlan'])+' STP 인스턴스'
    return None,'지원하지 않는 자동 조건'
