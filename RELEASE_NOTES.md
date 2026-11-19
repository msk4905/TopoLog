# TopoLog v1.2.2

Windows ZIP을 모두 풀고 `TopoLog/TopoLog.exe`를 실행하세요. GNS3와 장비 이미지는 기존 설치 환경을 사용합니다.

## 수집 명령 호환성

- 장비가 `show vlan brief`를 지원하지 않으면 응답을 확인한 뒤 `show vlan-switch brief`를 시도합니다. 명령 시도 기록과 원문을 보존하고, 보고서에서는 실제로 VLAN 목록을 반환한 명령을 근거로 연결합니다. [GitHub #1](https://github.com/msk4905/TopoLog/issues/1)
- 스위칭 구성과 STP 확인 항목에 맞춰 `show spanning-tree`를 수집합니다. EtherSwitch와 Catalyst의 STP 인스턴스 출력을 처리합니다.
- ACL 적용 위치를 찾을 때 공백·탭·대소문자 차이를 처리합니다.

## GNS3 연동

- 선택한 프로젝트에 GNS3 VM 장비가 있으면 해당 VM의 연결을 기다립니다. 로컬 Dynamips·VPCS 장비와 별도 원격 compute 장비는 각 연결을 사용합니다.
- Windows에서 콘솔 포트가 충돌하면 해당 장비의 시작을 다시 시도합니다. GNS3 토폴로지 사진 버튼 호출의 일시적인 오류에도 재시도합니다.
- 프로젝트 ID가 겹치면 GNS3 서버가 지정한 위치에 임시 사본을 만들어 수집합니다. 사본의 소유 정보와 위치를 확인한 뒤 정리합니다.

## 보고서와 근거

- IKE·IPsec 상태를 해당 피어의 출력으로 확인하고, IPv6 주소를 해당 인터페이스의 출력과 대조합니다.
- 사용자 지정 ping은 실제로 실행한 명령 및 응답과 연결합니다. 최종 상태 재조회가 중단되면 시도한 명령과 실행 상태를 기록합니다.
- 기존 수집 JSON을 읽고, 새 수집에는 명령 응답 상태와 대체 명령의 관계를 함께 기록합니다.

[변경 이력](CHANGELOG.md) · [검증 기록](VALIDATION.md)
