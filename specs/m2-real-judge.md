# M2 — настоящий судья и первый live-прогон

Статус: **готово** (01.11.2026). Открыт только независимый замер правила двух сигналов — переходит в M3/M4.

## Сделано
| # | Критерий | Проверка |
|---|----------|----------|
| 1 | Судья-модель: Claude Code headless (`claude -p`), ответ строго по JSON-схеме из атрибутов брифа | `factory/backends/claude_judge.py`, `test_claude_judge_schema` |
| 2 | Судья сравнивает кадр с фото товара, а не описывает кадр сам по себе | `with_reference=True`, замер в `results/judge-evals.md` |
| 3 | Mock рисует дефекты на картинке, а не подписывает их текстом — подсказка не утекает судье | `factory/backends/render.py`, `test_defect_is_drawn_not_written` |
| 4 | Замер судьи на кадрах с известной истиной: recall брака, ложный брак, точность атрибутов, с/кадр | `factory eval-judge`, `TestJudgeEval` |
| 5 | Модель судьи выбрана замером: Sonnet 36/36 против Haiku 70–91 % | `results/judge-evals.md` |
| 6 | В метриках прогона — верный вердикт судьи и точные атрибуты (в mock истина известна) | `judge_verdict_accuracy`, `judge_attrs_exact` |
| 7 | Дашборд по прогонам и замерам | `factory dashboard` |
| 8 | CI: тесты и бенч на каждый push | `ci/github-tests.yml` — включить: перенести в `.github/workflows/` (нужно право `workflow` у токена gh) |

| 9 | Первый live-прогон: 4 шота, WaveSpeed Seedream 5 Pro, потолок $0.50 (одобрен пользователем) | `results/live-runs.md`: $0.173, 4/4 |
| 10 | Сбой сети не ведёт к повторной оплате: id задачи на диске, `--resume` | `TestResilience` |
| 11 | Реальные траты — по балансу провайдера до/после | `real_spend_usd` в metrics.json |
| 12 | Судья проверяет сцену по брифу шота; отказ по сцене не пишется в память товара | `scene_ok`, `test_scene_reject_is_not_learned_into_memory`; rejudge live #1: поймал шот 3 |
| 13 | Цвет камня: ΔE76 в Lab, рамка от судьи, эталон `color_box`; порог откалиброван на реальных фото товара | `factory/color.py`, `TestColor`, `results/live-runs.md` |
| 14 | `factory rejudge` — перепроверка оплаченных кадров новым судьёй без генерации | `results/live-runs.md` |

## Осталось
- [x] Замер судьи на 47 реальных кадрах с ручной разметкой: 97,9 % при цели ≥ 90 % (`results/judge-evals.md`).
- [x] Два прохода для мелких объектов + правило двух сигналов (спор → `needs_review`); тест `test_zoom_crop_centers_small_earring`.
- [ ] Независимый замер правила двух сигналов на новых кадрах (не на тех, по которым оно придумано).
- [x] Видео: WaveSpeed Hailuo 2.3 i2v ($0.28, live) и перенос `rh_h3.py`; судья по кадрам ролика; `test_video_is_judged_separately`.
