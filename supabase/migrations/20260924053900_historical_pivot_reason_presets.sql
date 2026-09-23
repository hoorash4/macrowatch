create table public.historical_pivot_reason_presets (
  id bigint generated always as identity primary key,
  phrase text not null unique check (char_length(trim(phrase)) between 1 and 250),
  sort_order integer not null unique check (sort_order > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.historical_pivot_reason_presets enable row level security;
revoke all on public.historical_pivot_reason_presets from anon, authenticated;
grant select, insert, update, delete on public.historical_pivot_reason_presets to service_role;
grant usage, select on sequence public.historical_pivot_reason_presets_id_seq to service_role;

insert into public.historical_pivot_reason_presets (sort_order, phrase) values
  (1, '상승 추세가 멈추고 하락 전환을 가져 온 큰 변곡점 입니다.'),
  (2, '상승이 멈추고 고점권 횡보로 국면이 바뀌었습니다.'),
  (3, '상승 흐름에서 급등한 뒤 방향을 되돌렸고, 이후 하락 흐름이 이어졌습니다.'),
  (4, '급등 후 급락으로 이어지며 상승 흐름이 꺾인 전환점입니다.'),
  (5, '급등 후 추세적인 하락세로 전환됐습니다.'),
  (6, '이전 고점에 다시 도달했으나 넘어서지 못하면서 상승의 종료가 드러났습니다.'),
  (7, '하락 추세가 멈추고 상승 전환을 가져 온 큰 변곡점 입니다.'),
  (8, '하락이 멈추고 저점권 횡보로 국면이 바뀌었습니다.'),
  (9, '하락 흐름에서 급락한 뒤 방향을 되돌렸고, 이후 상승 흐름이 이어졌습니다.'),
  (10, '급락 후 급등으로 이어지며 하락 흐름이 꺾인 전환점입니다.'),
  (11, '급락 후 추세적인 상승세로 전환됐습니다.'),
  (12, '이전 저점을 다시 시험했으나 더 내려가지 않으면서 하락의 종료가 드러났습니다.'),
  (13, '횡보 범위를 위로 벗어나 새로운 상승 흐름이 시작됐습니다.'),
  (14, '횡보 범위를 아래로 벗어나 새로운 하락 흐름이 시작됐습니다.'),
  (15, '급등 이후 이전보다 높은 수준을 유지하며 흐름이 달라졌습니다.'),
  (16, '급락 이후 이전보다 낮은 수준에 머물며 흐름이 달라졌습니다.'),
  (17, '기존 추세를 크게 뛰어넘는 급등의 시작점입니다.'),
  (18, '기존 추세를 크게 밑도는 급락의 시작점입니다.'),
  (19, '이전/이후 추세가 불명확 합니다.');
