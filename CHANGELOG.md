# Changelog

## [0.1.0](https://github.com/mergesafe-ai/judgetap/compare/snapjudge-v0.0.1...snapjudge-v0.1.0) (2026-09-27)


### Features

* **cascade:** escalate low-confidence answers across engines ([#16](https://github.com/mergesafe-ai/judgetap/issues/16)) ([d062390](https://github.com/mergesafe-ai/judgetap/commit/d06239011f42b5b5773a651771b1e77c1edc6aae))
* **core:** typed decision API with validated Decision results ([#9](https://github.com/mergesafe-ai/judgetap/issues/9)) ([20d8af9](https://github.com/mergesafe-ai/judgetap/commit/20d8af9542acce650425af60232fdd8414e4f6b2)), closes [#1](https://github.com/mergesafe-ai/judgetap/issues/1)
* **dashboard:** local page over the guard log ([#25](https://github.com/mergesafe-ai/judgetap/issues/25)) ([1afa1a1](https://github.com/mergesafe-ai/judgetap/commit/1afa1a10817ab5ce7653e48b8cc14bdfeaef65d9))
* engine keys from the OS keychain ([#48](https://github.com/mergesafe-ai/judgetap/issues/48)) ([3da0065](https://github.com/mergesafe-ai/judgetap/commit/3da006589f3f661d356c4d796a77c07225e176d8))
* **engines:** any TypeSafe-compatible server by base URL ([#45](https://github.com/mergesafe-ai/judgetap/issues/45)) ([6525e65](https://github.com/mergesafe-ai/judgetap/commit/6525e652468d4df62856c424106e1466d6b118b6))
* **engines:** Jev and LiteLLM adapters with spec-string loading ([#12](https://github.com/mergesafe-ai/judgetap/issues/12)) ([93400ef](https://github.com/mergesafe-ai/judgetap/commit/93400ef438c5093259b1c122b5133dbef35db6bb)), closes [#2](https://github.com/mergesafe-ai/judgetap/issues/2)
* **engines:** local Laya and AgentJev adapters ([#18](https://github.com/mergesafe-ai/judgetap/issues/18)) ([08363ba](https://github.com/mergesafe-ai/judgetap/commit/08363ba7c287abfc8e3b32018d779450f867b601))
* **eval:** calibration check across engines ([#20](https://github.com/mergesafe-ai/judgetap/issues/20)) ([c747835](https://github.com/mergesafe-ai/judgetap/commit/c74783502f2ddc1ee6d95bbff680986710e750e0))
* **guard:** Cursor and Codex support ([#24](https://github.com/mergesafe-ai/judgetap/issues/24)) ([654a807](https://github.com/mergesafe-ai/judgetap/commit/654a8078251eb163033bbc1e361d3ea4ed97ba55))
* **guard:** default engine: detect and record, else rules only; register snapjudge eval ([#23](https://github.com/mergesafe-ai/judgetap/issues/23)) ([5a36d92](https://github.com/mergesafe-ai/judgetap/commit/5a36d9252c5c0077878e6eb86c70cc4fe028adc7))
* **guard:** pre-action guard for Claude Code ([#17](https://github.com/mergesafe-ai/judgetap/issues/17)) ([b5e7f3b](https://github.com/mergesafe-ai/judgetap/commit/b5e7f3bd87cac7af8a651626caa26c99ab4d6900))
* opt-in library decision log shown on the dashboard ([#46](https://github.com/mergesafe-ai/judgetap/issues/46)) ([7adcec3](https://github.com/mergesafe-ai/judgetap/commit/7adcec38ff737cd43e116c826b963c4da7ed9c80))


### Bug Fixes

* **core:** harden question and answer validation (review of [#9](https://github.com/mergesafe-ai/judgetap/issues/9)) ([#11](https://github.com/mergesafe-ai/judgetap/issues/11)) ([56e0d0f](https://github.com/mergesafe-ai/judgetap/commit/56e0d0f979b693c4dff20c62f25b0b96c7a29f5b))
* **dashboard:** true call counts, cached polling, Content-Length 400, empty chart message ([#36](https://github.com/mergesafe-ai/judgetap/issues/36)) ([435524e](https://github.com/mergesafe-ai/judgetap/commit/435524ee225d72c17cbf87e39b1537a0d8b95cef)), closes [#35](https://github.com/mergesafe-ai/judgetap/issues/35)
* **guard:** combined git clean flags; global/system push config ([#22](https://github.com/mergesafe-ai/judgetap/issues/22)) ([93b8a1f](https://github.com/mergesafe-ai/judgetap/commit/93b8a1f72588006577f9e22dd03f92ce7ddecf23))
* **guard:** redact basic-auth, bearer and more token formats ([#37](https://github.com/mergesafe-ai/judgetap/issues/37)) ([38a4d82](https://github.com/mergesafe-ai/judgetap/commit/38a4d82256aca391c63d0a77e6a0117d63cb8db4))
* review follow-ups from [#11](https://github.com/mergesafe-ai/judgetap/issues/11), [#12](https://github.com/mergesafe-ai/judgetap/issues/12), [#16](https://github.com/mergesafe-ai/judgetap/issues/16); snapjudge.toml cascade config ([#19](https://github.com/mergesafe-ai/judgetap/issues/19)) ([419641f](https://github.com/mergesafe-ai/judgetap/commit/419641fa582d3f4b03bd7d48ade70390afc99834))


### Documentation

* add CONTRIBUTING.md ([#44](https://github.com/mergesafe-ai/judgetap/issues/44)) ([d81f1dd](https://github.com/mergesafe-ai/judgetap/commit/d81f1ddceea275d80b3e04ce2468ceec7c5f8d5d)), closes [#40](https://github.com/mergesafe-ai/judgetap/issues/40)
* launch README, demo tape and render workflow ([#49](https://github.com/mergesafe-ai/judgetap/issues/49)) ([33f3c04](https://github.com/mergesafe-ai/judgetap/commit/33f3c04c9fc233b7b724e4f1be48c798c5fc9fde))
