-- ONE-TIME BOOTSTRAP ONLY: initial domestic sector-flow registry.
--
-- Do not replay this data seed during deployment. After the initial bootstrap,
-- the administrator-managed rows (including deletions) are authoritative.
-- Names describe the representative ETF's actual exposure; they are not
-- broader labels inherited from the dashboard planning list.
create unique index if not exists market_sector_etfs_sector_name_uidx
  on public.market_sector_etfs (sector_name);

insert into public.market_sector_etfs
  (sector_name, etf_name, etf_ticker, issuer)
values
  ('반도체', 'KODEX 반도체', '091160', '삼성자산운용'),
  ('AI반도체소부장', 'SOL AI반도체소부장', '455850', '신한자산운용'),
  ('2차전지산업', 'KODEX 2차전지산업', '305720', '삼성자산운용'),
  ('자동차', 'KODEX 자동차', '091180', '삼성자산운용'),
  ('조선TOP10', 'TIGER 조선TOP10', '494670', '미래에셋자산운용'),
  ('K방산', 'PLUS K방산', '449450', '한화자산운용'),
  ('기계장비', 'KODEX 기계장비', '102960', '삼성자산운용'),
  ('AI전력핵심설비', 'KODEX AI전력핵심설비', '487240', '삼성자산운용'),
  ('원자력TOP10', 'ACE 원자력TOP10', '433500', '한국투자신탁운용'),
  ('건설', 'KODEX 건설', '117700', '삼성자산운용'),
  ('철강', 'KODEX 철강', '117680', '삼성자산운용'),
  ('에너지화학', 'KODEX 에너지화학', '117460', '삼성자산운용'),
  ('친환경에너지', 'HANARO Fn친환경에너지', '381570', 'NH-Amundi자산운용'),
  ('200 금융', 'TIGER 200 금융', '139270', '미래에셋자산운용'),
  ('은행', 'KODEX 은행', '091170', '삼성자산운용'),
  ('증권', 'KODEX 증권', '102970', '삼성자산운용'),
  ('보험', 'KODEX 보험', '140700', '삼성자산운용'),
  ('바이오', 'KODEX 바이오', '244580', '삼성자산운용'),
  ('필수소비재', 'KODEX 필수소비재', '266410', '삼성자산운용'),
  ('경기소비재', 'KODEX 경기소비재', '266390', '삼성자산운용'),
  ('운송', 'KODEX 운송', '140710', '삼성자산운용'),
  ('조선해운', 'HANARO Fn조선해운', '441540', 'NH-Amundi자산운용'),
  ('네트워크인프라', 'RISE 네트워크인프라', '367760', 'KB자산운용'),
  ('인터넷TOP10', 'TIGER 인터넷TOP10', '365000', '미래에셋자산운용'),
  ('AI&로봇', 'RISE AI&로봇', '469070', 'KB자산운용'),
  ('자율주행', 'KODEX 자율주행액티브', '385520', '삼성자산운용'),
  ('우주항공', 'PLUS 우주항공', '421320', '한화자산운용'),
  ('Fn신재생에너지', 'TIGER Fn신재생에너지', '377990', '미래에셋자산운용'),
  ('태양광&ESS', 'PLUS 태양광&ESS', '457990', '한화자산운용'),
  ('화장품', 'TIGER 화장품', '228790', '미래에셋자산운용'),
  ('K게임', 'TIGER K게임', '300610', '미래에셋자산운용'),
  ('K콘텐츠', 'KODEX K콘텐츠', '266360', '삼성자산운용'),
  ('웹툰&드라마', 'KODEX 웹툰&드라마', '395150', '삼성자산운용'),
  ('Fn K-푸드', 'HANARO Fn K-푸드', '438900', 'NH-Amundi자산운용'),
  ('여행레저', 'TIGER 여행레저', '228800', '미래에셋자산운용'),
  ('Fn메타버스', 'TIGER Fn메타버스', '400970', '미래에셋자산운용'),
  ('탄소효율그린뉴딜', 'KODEX 탄소효율그린뉴딜', '375770', '삼성자산운용'),
  ('한국부동산리츠인프라', 'KODEX 한국부동산리츠인프라', '476800', '삼성자산운용'),
  ('수소경제테마', 'RISE 수소경제테마', '367770', 'KB자산운용')
on conflict (etf_ticker)
do nothing;

comment on index public.market_sector_etfs_sector_name_uidx is
  'Enforces one representative ETF per displayed sector.';
