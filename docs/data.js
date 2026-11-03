window.TOPOLOG_DEMO_DATA = {
  "project": "20260827-1",
  "node": "R1",
  "nodeCount": 4,
  "capturedAt": "2026-11-03T14:57:30.5852202+09:00",
  "fullNeighbors": 2,
  "commands": [
    {
      "command": "show ip interface brief",
      "image": "assets/01_show_ip_interface_brief.png",
      "text": "show ip interface brief\nInterface                  IP-Address      OK? Method Status                Protocol\nFastEthernet0/0            unassigned      YES NVRAM  administratively down down    \nSerial1/0                  unassigned      YES NVRAM  administratively down down    \nSerial1/1                  192.168.12.1    YES NVRAM  up                    up      \nSerial1/2                  192.168.14.2    YES NVRAM  up                    up      \nSerial1/3                  unassigned      YES NVRAM  administratively down down    \nSSLVPN-VIF0                unassigned      NO  unset  up                    up      \nLoopback0                  172.16.1.1      YES NVRAM  up                    up      \nR1#",
      "cliError": false
    },
    {
      "command": "show ip ospf neighbor",
      "image": "assets/02_show_ip_ospf_neighbor.png",
      "text": "show ip ospf neighbor\n\nNeighbor ID     Pri   State           Dead Time   Address         Interface\n4.4.4.4           0   FULL/  -        00:00:37    192.168.14.1    Serial1/2\n2.2.2.2           0   FULL/  -        00:00:39    192.168.12.2    Serial1/1\nR1#",
      "cliError": false
    },
    {
      "command": "show ip route",
      "image": "assets/03_show_ip_route.png",
      "text": "show ip route\nCodes: C - connected, S - static, R - RIP, M - mobile, B - BGP\n       D - EIGRP, EX - EIGRP external, O - OSPF, IA - OSPF inter area \n       N1 - OSPF NSSA external type 1, N2 - OSPF NSSA external type 2\n       E1 - OSPF external type 1, E2 - OSPF external type 2\n       i - IS-IS, su - IS-IS summary, L1 - IS-IS level-1, L2 - IS-IS level-2\n       ia - IS-IS inter area, * - candidate default, U - per-user static route\n       o - ODR, P - periodic downloaded static route\n\nGateway of last resort is not set\n\nC    192.168.12.0/24 is directly connected, Serial1/1\nC    192.168.14.0/24 is directly connected, Serial1/2\n     172.16.0.0/24 is subnetted, 4 subnets\nO       172.16.4.0 [110/101] via 192.168.14.1, 00:51:08, Serial1/2\nC       172.16.1.0 is directly connected, Loopback0\nO       172.16.2.0 [110/11] via 192.168.12.2, 00:51:18, Serial1/1\nO       172.16.3.0 [110/21] via 192.168.12.2, 00:51:18, Serial1/1\nO    192.168.23.0/24 [110/20] via 192.168.12.2, 00:51:18, Serial1/1\nO    192.168.34.0/24 [110/120] via 192.168.12.2, 00:51:18, Serial1/1\nR1#",
      "cliError": false
    }
  ],
  "prompt": "첨부한 GNS3 종합 자료를 아래 목적에 맞는 문서로 편집해 주세요.\n\n문서 목적: [기술 보고서 / 취업 포트폴리오 / 학습 정리 / 기타]\n독자: [직접 입력]\n원하는 분량과 형식: [직접 입력]\n강조하고 싶은 경험과 본인 기여도: [확인 가능한 내용만 직접 입력]\n\n규칙:\n1. report.md와 원본 로그·설정·토폴로지의 근거를 먼저 확인하세요.\n2. 저장 설정과 실행 시점 출력은 구분하세요. 서로 다르면 확인이 필요한 차이로 표시하세요.\n3. 설정 존재, 명령 수집 성공, 실제 기능 검증 성공을 구분하세요. CLI 오류가 없다는 것만으로 정상 동작을 확정하지 마세요.\n4. 실행하지 않은 테스트, 설계 의도, 본인 기여도, 장애 해결 경험, 성능 향상을 만들어내지 마세요.\n5. 실패·미검증·증거 부족 상태를 유지하고, 필요한 추가 질문을 정리하세요.\n6. 이미지별 출처를 확인하고 기존 기록지의 이미지, 새 화면 캡처, 토폴로지 재구성 이미지, 로그로 생성한 이미지를 구분하세요. 촬영 시각을 추측하지 마세요.\n7. 목적에 맞게 구성과 문장을 바꾸되 증거 연결과 이미지 상대경로를 유지하세요.\n8. 최종 문서와 추가 확인이 필요한 사항을 구분해 제시하세요."
};