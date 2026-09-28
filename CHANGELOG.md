# Changelog

## [0.3.0](https://github.com/mergesafe-ai/judgetap/compare/v0.2.0...v0.3.0) (2026-09-28)


### Features

* **engines:** GLiNER2.5-Decide engine ([#93](https://github.com/mergesafe-ai/judgetap/issues/93)) ([dae8a41](https://github.com/mergesafe-ai/judgetap/commit/dae8a4105994a59c84fe2ab33567b4a69a39829c))
* **engines:** Julia-1 on-device engine ([#90](https://github.com/mergesafe-ai/judgetap/issues/90)) ([97538c1](https://github.com/mergesafe-ai/judgetap/commit/97538c125c0fc6bc4a1f7f89ed1ac5b45f6293e3))
* **engines:** llm logprobs mode for calibrated label probabilities ([#91](https://github.com/mergesafe-ai/judgetap/issues/91)) ([b0494f4](https://github.com/mergesafe-ai/judgetap/commit/b0494f4b71dfb673a8db2c4094395c79cd37fb02))
* **eval:** judgetap eval --suite loader for public suites (AG News, Banking77) ([#99](https://github.com/mergesafe-ai/judgetap/issues/99)) ([2d3c54c](https://github.com/mergesafe-ai/judgetap/commit/2d3c54caf87afd89c7f4c7c8e515298606e90866))
* **guard:** shadow-mode output pruning estimate ([#27](https://github.com/mergesafe-ai/judgetap/issues/27), [#29](https://github.com/mergesafe-ai/judgetap/issues/29)) ([#110](https://github.com/mergesafe-ai/judgetap/issues/110)) ([6254ace](https://github.com/mergesafe-ai/judgetap/commit/6254acec399f9d67a4b1470d72edb454ed3d754f))


### Bug Fixes

* follow-ups from [#105](https://github.com/mergesafe-ai/judgetap/issues/105) and [#107](https://github.com/mergesafe-ai/judgetap/issues/107) ([#109](https://github.com/mergesafe-ai/judgetap/issues/109)) ([b4df5da](https://github.com/mergesafe-ai/judgetap/commit/b4df5da45cdb623c4827a5dd3122a05ff183598c))
* follow-ups from [#91](https://github.com/mergesafe-ai/judgetap/issues/91) and [#93](https://github.com/mergesafe-ai/judgetap/issues/93) (llm logprobs calls, hook missing tool name) ([#105](https://github.com/mergesafe-ai/judgetap/issues/105)) ([a280f26](https://github.com/mergesafe-ai/judgetap/commit/a280f2668748dd9764f859c63da2c9e9a2975662))
* **guard:** repo rules can't talk the judge past a rules-only ask ([#100](https://github.com/mergesafe-ai/judgetap/issues/100)) ([#103](https://github.com/mergesafe-ai/judgetap/issues/103)) ([0dcf8af](https://github.com/mergesafe-ai/judgetap/commit/0dcf8afaf54b4c089a51bb730454ddf3fd53f180))
* **julia:** models-dir install docs, reject ../ escapes, bound model cache ([#96](https://github.com/mergesafe-ai/judgetap/issues/96)) ([1d0b509](https://github.com/mergesafe-ai/judgetap/commit/1d0b5099fa9889766e426c5e0e4ccecc4cfc125c))
* plain reason for rules-capped judge allow; non-vacuous concurrent cache tests ([#106](https://github.com/mergesafe-ai/judgetap/issues/106)) ([#107](https://github.com/mergesafe-ai/judgetap/issues/107)) ([43492da](https://github.com/mergesafe-ai/judgetap/commit/43492daa9c307769cad5521ca731254e1ea92b1b))
* route every model call through the LRU cache; report malformed row index once ([#98](https://github.com/mergesafe-ai/judgetap/issues/98), [#101](https://github.com/mergesafe-ai/judgetap/issues/101)) ([#104](https://github.com/mergesafe-ai/judgetap/issues/104)) ([6656556](https://github.com/mergesafe-ai/judgetap/commit/66565565ddb5a5412080394071a1005499bce269))


### Documentation

* restore the name and link of Micha0827/snapjudge (broken by the rename) ([#92](https://github.com/mergesafe-ai/judgetap/issues/92)) ([f27c172](https://github.com/mergesafe-ai/judgetap/commit/f27c1720ca5e31a67de9b1c80d1104377582311c))

## [0.2.0](https://github.com/mergesafe-ai/judgetap/compare/v0.1.0...v0.2.0) (2026-09-27)


### ⚠ BREAKING CHANGES

* rename snapjudge to judgetap ([#52](https://github.com/mergesafe-ai/judgetap/issues/52))

### Features

* **cascade:** escalate low-confidence answers across engines ([#16](https://github.com/mergesafe-ai/judgetap/issues/16)) ([d062390](https://github.com/mergesafe-ai/judgetap/commit/d06239011f42b5b5773a651771b1e77c1edc6aae))
* **core:** typed decision API with validated Decision results ([#9](https://github.com/mergesafe-ai/judgetap/issues/9)) ([20d8af9](https://github.com/mergesafe-ai/judgetap/commit/20d8af9542acce650425af60232fdd8414e4f6b2)), closes [#1](https://github.com/mergesafe-ai/judgetap/issues/1)
* **dashboard:** local page over the guard log ([#25](https://github.com/mergesafe-ai/judgetap/issues/25)) ([1afa1a1](https://github.com/mergesafe-ai/judgetap/commit/1afa1a10817ab5ce7653e48b8cc14bdfeaef65d9))
* engine keys from the OS keychain ([#48](https://github.com/mergesafe-ai/judgetap/issues/48)) ([3da0065](https://github.com/mergesafe-ai/judgetap/commit/3da006589f3f661d356c4d796a77c07225e176d8))
* engines report calls; dashboard engine metrics from calls ([#58](https://github.com/mergesafe-ai/judgetap/issues/58)) ([a54c77a](https://github.com/mergesafe-ai/judgetap/commit/a54c77a67dfc33e0e1856dda5624043367dc6c3f))
* **engines:** any TypeSafe-compatible server by base URL ([#45](https://github.com/mergesafe-ai/judgetap/issues/45)) ([6525e65](https://github.com/mergesafe-ai/judgetap/commit/6525e652468d4df62856c424106e1466d6b118b6))
* **engines:** Jev and LiteLLM adapters with spec-string loading ([#12](https://github.com/mergesafe-ai/judgetap/issues/12)) ([93400ef](https://github.com/mergesafe-ai/judgetap/commit/93400ef438c5093259b1c122b5133dbef35db6bb)), closes [#2](https://github.com/mergesafe-ai/judgetap/issues/2)
* **engines:** local Laya and AgentJev adapters ([#18](https://github.com/mergesafe-ai/judgetap/issues/18)) ([08363ba](https://github.com/mergesafe-ai/judgetap/commit/08363ba7c287abfc8e3b32018d779450f867b601))
* **eval:** calibration check across engines ([#20](https://github.com/mergesafe-ai/judgetap/issues/20)) ([c747835](https://github.com/mergesafe-ai/judgetap/commit/c74783502f2ddc1ee6d95bbff680986710e750e0))
* **guard:** Cursor and Codex support ([#24](https://github.com/mergesafe-ai/judgetap/issues/24)) ([654a807](https://github.com/mergesafe-ai/judgetap/commit/654a8078251eb163033bbc1e361d3ea4ed97ba55))
* **guard:** default engine: detect and record, else rules only; register snapjudge eval ([#23](https://github.com/mergesafe-ai/judgetap/issues/23)) ([5a36d92](https://github.com/mergesafe-ai/judgetap/commit/5a36d9252c5c0077878e6eb86c70cc4fe028adc7))
* **guard:** loop detection on PostToolUse ([#54](https://github.com/mergesafe-ai/judgetap/issues/54)) ([a2a16d4](https://github.com/mergesafe-ai/judgetap/commit/a2a16d40c8c7b76caf5ff6daeb9cf96ccd60d966))
* **guard:** opt-in task-done check on Stop ([#55](https://github.com/mergesafe-ai/judgetap/issues/55)) ([4923eed](https://github.com/mergesafe-ai/judgetap/commit/4923eed1d8de5c504982d1409666d8a91d361540)), closes [#26](https://github.com/mergesafe-ai/judgetap/issues/26)
* **guard:** pre-action guard for Claude Code ([#17](https://github.com/mergesafe-ai/judgetap/issues/17)) ([b5e7f3b](https://github.com/mergesafe-ai/judgetap/commit/b5e7f3bd87cac7af8a651626caa26c99ab4d6900))
* opt-in library decision log shown on the dashboard ([#46](https://github.com/mergesafe-ai/judgetap/issues/46)) ([7adcec3](https://github.com/mergesafe-ai/judgetap/commit/7adcec38ff737cd43e116c826b963c4da7ed9c80))
* rename snapjudge to judgetap ([#52](https://github.com/mergesafe-ai/judgetap/issues/52)) ([19f38cc](https://github.com/mergesafe-ai/judgetap/commit/19f38cc78a1e21f9bb1bb282e80290bd40676803))


### Bug Fixes

* cascade and API keep calls and cost on partial failures ([#70](https://github.com/mergesafe-ai/judgetap/issues/70)) ([be33207](https://github.com/mergesafe-ai/judgetap/commit/be33207aa98a791698b3070e9824da4075cabdc9))
* **core:** harden question and answer validation (review of [#9](https://github.com/mergesafe-ai/judgetap/issues/9)) ([#11](https://github.com/mergesafe-ai/judgetap/issues/11)) ([56e0d0f](https://github.com/mergesafe-ai/judgetap/commit/56e0d0f979b693c4dff20c62f25b0b96c7a29f5b))
* **dashboard:** library layer filter, source-aware outcomes, redact library answers ([#53](https://github.com/mergesafe-ai/judgetap/issues/53)) ([e53fca9](https://github.com/mergesafe-ai/judgetap/commit/e53fca94861732350c90fe2967286bfff121beeb)), closes [#51](https://github.com/mergesafe-ai/judgetap/issues/51)
* **dashboard:** odd log values, strict JSON, bound port; keys/env fixes ([#72](https://github.com/mergesafe-ai/judgetap/issues/72)) ([d9235eb](https://github.com/mergesafe-ai/judgetap/commit/d9235eb348ae94207d311e8f772c6fce3444a455))
* **dashboard:** true call counts, cached polling, Content-Length 400, empty chart message ([#36](https://github.com/mergesafe-ai/judgetap/issues/36)) ([435524e](https://github.com/mergesafe-ai/judgetap/commit/435524ee225d72c17cbf87e39b1537a0d8b95cef)), closes [#35](https://github.com/mergesafe-ai/judgetap/issues/35)
* **guard:** a failed stop judge logs the calls it made and fails open ([#59](https://github.com/mergesafe-ai/judgetap/issues/59)) ([8813bfe](https://github.com/mergesafe-ai/judgetap/commit/8813bfe4a9873f75c1f5ef87493361a8df96371c))
* **guard:** combined git clean flags; global/system push config ([#22](https://github.com/mergesafe-ai/judgetap/issues/22)) ([93b8a1f](https://github.com/mergesafe-ai/judgetap/commit/93b8a1f72588006577f9e22dd03f92ce7ddecf23))
* **guard:** config names match only as whole filenames ([#82](https://github.com/mergesafe-ai/judgetap/issues/82)) ([40c4b47](https://github.com/mergesafe-ai/judgetap/commit/40c4b47f1da7f7f34f505e448adfb76743219e04)), closes [#81](https://github.com/mergesafe-ai/judgetap/issues/81)
* **guard:** hooks match Claude Code's contract (PostToolUseFailure, last_assistant_message) ([#73](https://github.com/mergesafe-ai/judgetap/issues/73)) ([bc5867f](https://github.com/mergesafe-ai/judgetap/commit/bc5867fd8417f55e440d12f292aa70bcd8fa43f5))
* **guard:** polish from [#71](https://github.com/mergesafe-ai/judgetap/issues/71) review ([#78](https://github.com/mergesafe-ai/judgetap/issues/78)) ([98224d0](https://github.com/mergesafe-ai/judgetap/commit/98224d084b5856c36d02e3ecfe10d451a361c0e2)), closes [#76](https://github.com/mergesafe-ai/judgetap/issues/76)
* **guard:** redact basic-auth, bearer and more token formats ([#37](https://github.com/mergesafe-ai/judgetap/issues/37)) ([38a4d82](https://github.com/mergesafe-ai/judgetap/commit/38a4d82256aca391c63d0a77e6a0117d63cb8db4))
* **guard:** repo config can only tighten; odd input can't fail open; redact common secrets ([#71](https://github.com/mergesafe-ai/judgetap/issues/71)) ([e43db9b](https://github.com/mergesafe-ai/judgetap/commit/e43db9b4641a035b78b92d125b440381f751a995))
* review follow-ups from [#11](https://github.com/mergesafe-ai/judgetap/issues/11), [#12](https://github.com/mergesafe-ai/judgetap/issues/12), [#16](https://github.com/mergesafe-ai/judgetap/issues/16); snapjudge.toml cascade config ([#19](https://github.com/mergesafe-ai/judgetap/issues/19)) ([419641f](https://github.com/mergesafe-ai/judgetap/commit/419641fa582d3f4b03bd7d48ade70390afc99834))


### Performance Improvements

* **guard:** stop hook reads the transcript window once ([#57](https://github.com/mergesafe-ai/judgetap/issues/57)) ([305306c](https://github.com/mergesafe-ai/judgetap/commit/305306cc758d0c0eaf0a1cad8bd5eae9a03750dd)), closes [#56](https://github.com/mergesafe-ai/judgetap/issues/56)


### Documentation

* add CONTRIBUTING.md ([#44](https://github.com/mergesafe-ai/judgetap/issues/44)) ([d81f1dd](https://github.com/mergesafe-ai/judgetap/commit/d81f1ddceea275d80b3e04ce2468ceec7c5f8d5d)), closes [#40](https://github.com/mergesafe-ai/judgetap/issues/40)
* launch README, demo tape and render workflow ([#49](https://github.com/mergesafe-ai/judgetap/issues/49)) ([33f3c04](https://github.com/mergesafe-ai/judgetap/commit/33f3c04c9fc233b7b724e4f1be48c798c5fc9fde))

## [0.1.0](https://github.com/mergesafe-ai/judgetap/compare/judgetap-v0.0.1...judgetap-v0.1.0) (2026-09-27)


### ⚠ BREAKING CHANGES

* rename snapjudge to judgetap ([#52](https://github.com/mergesafe-ai/judgetap/issues/52))

### Features

* **cascade:** escalate low-confidence answers across engines ([#16](https://github.com/mergesafe-ai/judgetap/issues/16)) ([d062390](https://github.com/mergesafe-ai/judgetap/commit/d06239011f42b5b5773a651771b1e77c1edc6aae))
* **core:** typed decision API with validated Decision results ([#9](https://github.com/mergesafe-ai/judgetap/issues/9)) ([20d8af9](https://github.com/mergesafe-ai/judgetap/commit/20d8af9542acce650425af60232fdd8414e4f6b2)), closes [#1](https://github.com/mergesafe-ai/judgetap/issues/1)
* **dashboard:** local page over the guard log ([#25](https://github.com/mergesafe-ai/judgetap/issues/25)) ([1afa1a1](https://github.com/mergesafe-ai/judgetap/commit/1afa1a10817ab5ce7653e48b8cc14bdfeaef65d9))
* engine keys from the OS keychain ([#48](https://github.com/mergesafe-ai/judgetap/issues/48)) ([3da0065](https://github.com/mergesafe-ai/judgetap/commit/3da006589f3f661d356c4d796a77c07225e176d8))
* engines report calls; dashboard engine metrics from calls ([#58](https://github.com/mergesafe-ai/judgetap/issues/58)) ([a54c77a](https://github.com/mergesafe-ai/judgetap/commit/a54c77a67dfc33e0e1856dda5624043367dc6c3f))
* **engines:** any TypeSafe-compatible server by base URL ([#45](https://github.com/mergesafe-ai/judgetap/issues/45)) ([6525e65](https://github.com/mergesafe-ai/judgetap/commit/6525e652468d4df62856c424106e1466d6b118b6))
* **engines:** Jev and LiteLLM adapters with spec-string loading ([#12](https://github.com/mergesafe-ai/judgetap/issues/12)) ([93400ef](https://github.com/mergesafe-ai/judgetap/commit/93400ef438c5093259b1c122b5133dbef35db6bb)), closes [#2](https://github.com/mergesafe-ai/judgetap/issues/2)
* **engines:** local Laya and AgentJev adapters ([#18](https://github.com/mergesafe-ai/judgetap/issues/18)) ([08363ba](https://github.com/mergesafe-ai/judgetap/commit/08363ba7c287abfc8e3b32018d779450f867b601))
* **eval:** calibration check across engines ([#20](https://github.com/mergesafe-ai/judgetap/issues/20)) ([c747835](https://github.com/mergesafe-ai/judgetap/commit/c74783502f2ddc1ee6d95bbff680986710e750e0))
* **guard:** Cursor and Codex support ([#24](https://github.com/mergesafe-ai/judgetap/issues/24)) ([654a807](https://github.com/mergesafe-ai/judgetap/commit/654a8078251eb163033bbc1e361d3ea4ed97ba55))
* **guard:** default engine: detect and record, else rules only; register snapjudge eval ([#23](https://github.com/mergesafe-ai/judgetap/issues/23)) ([5a36d92](https://github.com/mergesafe-ai/judgetap/commit/5a36d9252c5c0077878e6eb86c70cc4fe028adc7))
* **guard:** loop detection on PostToolUse ([#54](https://github.com/mergesafe-ai/judgetap/issues/54)) ([a2a16d4](https://github.com/mergesafe-ai/judgetap/commit/a2a16d40c8c7b76caf5ff6daeb9cf96ccd60d966))
* **guard:** opt-in task-done check on Stop ([#55](https://github.com/mergesafe-ai/judgetap/issues/55)) ([4923eed](https://github.com/mergesafe-ai/judgetap/commit/4923eed1d8de5c504982d1409666d8a91d361540)), closes [#26](https://github.com/mergesafe-ai/judgetap/issues/26)
* **guard:** pre-action guard for Claude Code ([#17](https://github.com/mergesafe-ai/judgetap/issues/17)) ([b5e7f3b](https://github.com/mergesafe-ai/judgetap/commit/b5e7f3bd87cac7af8a651626caa26c99ab4d6900))
* opt-in library decision log shown on the dashboard ([#46](https://github.com/mergesafe-ai/judgetap/issues/46)) ([7adcec3](https://github.com/mergesafe-ai/judgetap/commit/7adcec38ff737cd43e116c826b963c4da7ed9c80))
* rename snapjudge to judgetap ([#52](https://github.com/mergesafe-ai/judgetap/issues/52)) ([19f38cc](https://github.com/mergesafe-ai/judgetap/commit/19f38cc78a1e21f9bb1bb282e80290bd40676803))


### Bug Fixes

* cascade and API keep calls and cost on partial failures ([#70](https://github.com/mergesafe-ai/judgetap/issues/70)) ([be33207](https://github.com/mergesafe-ai/judgetap/commit/be33207aa98a791698b3070e9824da4075cabdc9))
* **core:** harden question and answer validation (review of [#9](https://github.com/mergesafe-ai/judgetap/issues/9)) ([#11](https://github.com/mergesafe-ai/judgetap/issues/11)) ([56e0d0f](https://github.com/mergesafe-ai/judgetap/commit/56e0d0f979b693c4dff20c62f25b0b96c7a29f5b))
* **dashboard:** library layer filter, source-aware outcomes, redact library answers ([#53](https://github.com/mergesafe-ai/judgetap/issues/53)) ([e53fca9](https://github.com/mergesafe-ai/judgetap/commit/e53fca94861732350c90fe2967286bfff121beeb)), closes [#51](https://github.com/mergesafe-ai/judgetap/issues/51)
* **dashboard:** odd log values, strict JSON, bound port; keys/env fixes ([#72](https://github.com/mergesafe-ai/judgetap/issues/72)) ([d9235eb](https://github.com/mergesafe-ai/judgetap/commit/d9235eb348ae94207d311e8f772c6fce3444a455))
* **dashboard:** true call counts, cached polling, Content-Length 400, empty chart message ([#36](https://github.com/mergesafe-ai/judgetap/issues/36)) ([435524e](https://github.com/mergesafe-ai/judgetap/commit/435524ee225d72c17cbf87e39b1537a0d8b95cef)), closes [#35](https://github.com/mergesafe-ai/judgetap/issues/35)
* **guard:** a failed stop judge logs the calls it made and fails open ([#59](https://github.com/mergesafe-ai/judgetap/issues/59)) ([8813bfe](https://github.com/mergesafe-ai/judgetap/commit/8813bfe4a9873f75c1f5ef87493361a8df96371c))
* **guard:** combined git clean flags; global/system push config ([#22](https://github.com/mergesafe-ai/judgetap/issues/22)) ([93b8a1f](https://github.com/mergesafe-ai/judgetap/commit/93b8a1f72588006577f9e22dd03f92ce7ddecf23))
* **guard:** hooks match Claude Code's contract (PostToolUseFailure, last_assistant_message) ([#73](https://github.com/mergesafe-ai/judgetap/issues/73)) ([bc5867f](https://github.com/mergesafe-ai/judgetap/commit/bc5867fd8417f55e440d12f292aa70bcd8fa43f5))
* **guard:** redact basic-auth, bearer and more token formats ([#37](https://github.com/mergesafe-ai/judgetap/issues/37)) ([38a4d82](https://github.com/mergesafe-ai/judgetap/commit/38a4d82256aca391c63d0a77e6a0117d63cb8db4))
* **guard:** repo config can only tighten; odd input can't fail open; redact common secrets ([#71](https://github.com/mergesafe-ai/judgetap/issues/71)) ([e43db9b](https://github.com/mergesafe-ai/judgetap/commit/e43db9b4641a035b78b92d125b440381f751a995))
* review follow-ups from [#11](https://github.com/mergesafe-ai/judgetap/issues/11), [#12](https://github.com/mergesafe-ai/judgetap/issues/12), [#16](https://github.com/mergesafe-ai/judgetap/issues/16); snapjudge.toml cascade config ([#19](https://github.com/mergesafe-ai/judgetap/issues/19)) ([419641f](https://github.com/mergesafe-ai/judgetap/commit/419641fa582d3f4b03bd7d48ade70390afc99834))


### Performance Improvements

* **guard:** stop hook reads the transcript window once ([#57](https://github.com/mergesafe-ai/judgetap/issues/57)) ([305306c](https://github.com/mergesafe-ai/judgetap/commit/305306cc758d0c0eaf0a1cad8bd5eae9a03750dd)), closes [#56](https://github.com/mergesafe-ai/judgetap/issues/56)


### Documentation

* add CONTRIBUTING.md ([#44](https://github.com/mergesafe-ai/judgetap/issues/44)) ([d81f1dd](https://github.com/mergesafe-ai/judgetap/commit/d81f1ddceea275d80b3e04ce2468ceec7c5f8d5d)), closes [#40](https://github.com/mergesafe-ai/judgetap/issues/40)
* launch README, demo tape and render workflow ([#49](https://github.com/mergesafe-ai/judgetap/issues/49)) ([33f3c04](https://github.com/mergesafe-ai/judgetap/commit/33f3c04c9fc233b7b724e4f1be48c798c5fc9fde))
