# 🤖 Faceit Team Bot

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/Aiogram-3.x-green" alt="Aiogram">
  <img src="https://img.shields.io/badge/PostgreSQL-15%2B-blue" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/Celery-5.x-green" alt="Celery">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
  <img src="https://img.shields.io/badge/Status-Production%20Ready-brightgreen" alt="Status">
</p>

**Telegram бот для поиска тиммейтов в CS2 на платформе Faceit.** Умный алгоритм подбора игроков по статистике, рангу, предпочитаемым ролям и часовым поясам. Профессиональное решение для игроков, ищущих сбалансированную команду.

---

## ✨ Ключевые возможности

| Функция | Описание | Преимущество |
|---------|----------|--------------|
| **🎯 Умный поиск** | Подбор по ELO, ролям, картам, часовым поясам | Находит идеально совместимых партнеров |
| **📊 Faceit API** | Автоматическое получение статистики игроков | Актуальные данные ELO, KD, винрейт |
| **🤝 Система заявок** | Отправка/принятие заявок с встроенным чатом | Удобное общение перед игрой |
| **⚡ Фоновые задачи** | Уведомления через Celery + Redis | Не блокирует основной поток |
| **💳 VIP подписки** | Платежи через ЮKassa | Расширенные фильтры поиска |
| **🛡️ Защита от спама** | Кастомные middleware | Контроль активности пользователей |
| **📈 Аналитика** | Мониторинг активности и рейтинги | Улучшение алгоритмов поиска |

---

## Верхнеуровневая архитектурная схема
> <img alt="Image" width="2000" height="1506" src="https://private-user-images.githubusercontent.com/251053501/530083463-631bfa44-39a6-4575-bc7e-9d8732d5cdce.png?jwt=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3NjY2MDk0NTIsIm5iZiI6MTc2NjYwOTE1MiwicGF0aCI6Ii8yNTEwNTM1MDEvNTMwMDgzNDYzLTYzMWJmYTQ0LTM5YTYtNDU3NS1iYzdlLTlkODczMmQ1Y2RjZS5wbmc_WC1BbXotQWxnb3JpdGhtPUFXUzQtSE1BQy1TSEEyNTYmWC1BbXotQ3JlZGVudGlhbD1BS0lBVkNPRFlMU0E1M1BRSzRaQSUyRjIwMjUxMjI0JTJGdXMtZWFzdC0xJTJGczMlMkZhd3M0X3JlcXVlc3QmWC1BbXotRGF0ZT0yMDI1MTIyNFQyMDQ1NTJaJlgtQW16LUV4cGlyZXM9MzAwJlgtQW16LVNpZ25hdHVyZT1mMWZmN2M1NjQ2M2EyZjdhZThiZjE3Mzk1OTZjMjMxNjNkNTAzZDJmOTY2YTU3OWM5ZGQ1MjI2OWRhZjc0NThhJlgtQW16LVNpZ25lZEhlYWRlcnM9aG9zdCJ9.8x5C0BSihecFGMvzuOHS4kNpyNN3zieKkWxFElI6XgU">

## 🖼️ Скриншоты интерфейса

|Меню|
> <img alt="Image" width="1298" height="240" src="https://private-user-images.githubusercontent.com/251053501/530082610-df09cd73-7470-4931-a429-7dcd22cfbf12.png?jwt=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3NjY2MDk4MTMsIm5iZiI6MTc2NjYwOTUxMywicGF0aCI6Ii8yNTEwNTM1MDEvNTMwMDgyNjEwLWRmMDljZDczLTc0NzAtNDkzMS1hNDI5LTdkY2QyMmNmYmYxMi5wbmc_WC1BbXotQWxnb3JpdGhtPUFXUzQtSE1BQy1TSEEyNTYmWC1BbXotQ3JlZGVudGlhbD1BS0lBVkNPRFlMU0E1M1BRSzRaQSUyRjIwMjUxMjI0JTJGdXMtZWFzdC0xJTJGczMlMkZhd3M0X3JlcXVlc3QmWC1BbXotRGF0ZT0yMDI1MTIyNFQyMDUxNTNaJlgtQW16LUV4cGlyZXM9MzAwJlgtQW16LVNpZ25hdHVyZT00ZTA3NWVmNDk1YWJiM2VlZTFjMTRjNDY4ZGJhMGQ0Zjc4YzdjODUxY2JhOWNkOWYwOTFlNDcyOTBiMjAxY2E1JlgtQW16LVNpZ25lZEhlYWRlcnM9aG9zdCJ9.H7tcW-R_w6Wvrit6nt-APomOMIxps4GfrsxhTRRmLEI">

|Меню магазина|
> <img alt="Image" width="429" height="405" src="https://private-user-images.githubusercontent.com/251053501/530083266-9f53285b-902c-40c9-bf9a-f5bad1d3d13d.png?jwt=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3NjY2MDk4NTAsIm5iZiI6MTc2NjYwOTU1MCwicGF0aCI6Ii8yNTEwNTM1MDEvNTMwMDgzMjY2LTlmNTMyODViLTkwMmMtNDBjOS1iZjlhLWY1YmFkMWQzZDEzZC5wbmc_WC1BbXotQWxnb3JpdGhtPUFXUzQtSE1BQy1TSEEyNTYmWC1BbXotQ3JlZGVudGlhbD1BS0lBVkNPRFlMU0E1M1BRSzRaQSUyRjIwMjUxMjI0JTJGdXMtZWFzdC0xJTJGczMlMkZhd3M0X3JlcXVlc3QmWC1BbXotRGF0ZT0yMDI1MTIyNFQyMDUyMzBaJlgtQW16LUV4cGlyZXM9MzAwJlgtQW16LVNpZ25hdHVyZT00MjZhYmU4ZDQ4OGNkMGViMGM3OWZhMTkwYTIxOTQ5ZGFhY2MwZmU0ZjM0NmQ1YzczNmE5Njk1YmNjOTBmYmU2JlgtQW16LVNpZ25lZEhlYWRlcnM9aG9zdCJ9.lWZKjfnnmKUGz0iLydsVLekYLaGLWKgjfNIiFiI-Hcw">

|Пример выгрузки статистики в панели администратора|
> <img alt="Image" width="255" height="404" src="https://private-user-images.githubusercontent.com/251053501/530083299-ac640db6-75ed-4e01-9f9d-2b95c1b2e355.png?jwt=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3NjY2MDk4NzIsIm5iZiI6MTc2NjYwOTU3MiwicGF0aCI6Ii8yNTEwNTM1MDEvNTMwMDgzMjk5LWFjNjQwZGI2LTc1ZWQtNGUwMS05ZjlkLTJiOTVjMWIyZTM1NS5wbmc_WC1BbXotQWxnb3JpdGhtPUFXUzQtSE1BQy1TSEEyNTYmWC1BbXotQ3JlZGVudGlhbD1BS0lBVkNPRFlMU0E1M1BRSzRaQSUyRjIwMjUxMjI0JTJGdXMtZWFzdC0xJTJGczMlMkZhd3M0X3JlcXVlc3QmWC1BbXotRGF0ZT0yMDI1MTIyNFQyMDUyNTJaJlgtQW16LUV4cGlyZXM9MzAwJlgtQW16LVNpZ25hdHVyZT0yM2MxOGJiOWRhYTNhNDg0NGEzMzBjMmMxMzA4NTQ1N2YyZWYzZTEwMjQzNDQwZjRkZTdkZmQ4NDI4NTlhMTM1JlgtQW16LVNpZ25lZEhlYWRlcnM9aG9zdCJ9._zSBDyHobD_GWuZssvgBHbbhAG8q11JtbIJ0yNuFTco">

|Пример системного уведомления об успешности/неуспешности рассылки на пользователей|
> <img alt="Image" width="440" height="232" src="https://private-user-images.githubusercontent.com/251053501/530083339-db5dfaed-4d06-4939-973d-c97b29440f0b.png?jwt=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3NjY2MDk4OTIsIm5iZiI6MTc2NjYwOTU5MiwicGF0aCI6Ii8yNTEwNTM1MDEvNTMwMDgzMzM5LWRiNWRmYWVkLTRkMDYtNDkzOS05NzNkLWM5N2IyOTQ0MGYwYi5wbmc_WC1BbXotQWxnb3JpdGhtPUFXUzQtSE1BQy1TSEEyNTYmWC1BbXotQ3JlZGVudGlhbD1BS0lBVkNPRFlMU0E1M1BRSzRaQSUyRjIwMjUxMjI0JTJGdXMtZWFzdC0xJTJGczMlMkZhd3M0X3JlcXVlc3QmWC1BbXotRGF0ZT0yMDI1MTIyNFQyMDUzMTJaJlgtQW16LUV4cGlyZXM9MzAwJlgtQW16LVNpZ25hdHVyZT0wZDM2MDZkNDMxZmM1NzdiZTlhNzM2NzIxMDRlZDZjMGQwNGRkMmM1Y2M0NmRmNzk1MjVhZWQ5Y2IyMTU5ODRjJlgtQW16LVNpZ25lZEhlYWRlcnM9aG9zdCJ9.BRw0a32cEII9HFf0Kr5H_dHfgI9iUfqsOGoZ3AAnw08">

|Пример сообщения об ошибке от пользователя|
> <img alt="Image" width="438" height="105" src="https://private-user-images.githubusercontent.com/251053501/530083364-aa9ac95d-5bce-4c0d-8e62-fa0d2df17e4b.png?jwt=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3NjY2MDk5MDgsIm5iZiI6MTc2NjYwOTYwOCwicGF0aCI6Ii8yNTEwNTM1MDEvNTMwMDgzMzY0LWFhOWFjOTVkLTViY2UtNGMwZC04ZTYyLWZhMGQyZGYxN2U0Yi5wbmc_WC1BbXotQWxnb3JpdGhtPUFXUzQtSE1BQy1TSEEyNTYmWC1BbXotQ3JlZGVudGlhbD1BS0lBVkNPRFlMU0E1M1BRSzRaQSUyRjIwMjUxMjI0JTJGdXMtZWFzdC0xJTJGczMlMkZhd3M0X3JlcXVlc3QmWC1BbXotRGF0ZT0yMDI1MTIyNFQyMDUzMjhaJlgtQW16LUV4cGlyZXM9MzAwJlgtQW16LVNpZ25hdHVyZT1hYmE1ODE3OTRjNjZlYWI4NDg2Y2I5MzViYTlhNmJmZjMxNTU1NzQwODNiZjg0YzhhNGYzZmRjZTRhYzA4NjgxJlgtQW16LVNpZ25lZEhlYWRlcnM9aG9zdCJ9.XAzrEOM_PnZC5PVTL2CXSESCWYWDSv7J0fxhQwwdzAE">

|Пример системного уведомления во время поиска тиммейтов|
> <img alt="Image" width="436" height="128" src="https://private-user-images.githubusercontent.com/251053501/530083415-5f13736a-617a-40ef-bddd-9674bca0fe29.png?jwt=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3NjY2MTAwNzEsIm5iZiI6MTc2NjYwOTc3MSwicGF0aCI6Ii8yNTEwNTM1MDEvNTMwMDgzNDE1LTVmMTM3MzZhLTYxN2EtNDBlZi1iZGRkLTk2NzRiY2EwZmUyOS5wbmc_WC1BbXotQWxnb3JpdGhtPUFXUzQtSE1BQy1TSEEyNTYmWC1BbXotQ3JlZGVudGlhbD1BS0lBVkNPRFlMU0E1M1BRSzRaQSUyRjIwMjUxMjI0JTJGdXMtZWFzdC0xJTJGczMlMkZhd3M0X3JlcXVlc3QmWC1BbXotRGF0ZT0yMDI1MTIyNFQyMDU2MTFaJlgtQW16LUV4cGlyZXM9MzAwJlgtQW16LVNpZ25hdHVyZT0yZjA2MWZhODI3MTYwZjNkZTMxOGIyMGRmOTgyODYzZDFlMmVlYzFjNzhmYWY2OTRiNTM4MTY5OGI2MmFmZGU5JlgtQW16LVNpZ25lZEhlYWRlcnM9aG9zdCJ9.HVvR39lHcWALe4qVkg1E38FzQCM1uBvkLligrU6mj2M">

|Пример отображения статистики пользователя|
> <img alt="Image" width="271" height="163" src="https://private-user-images.githubusercontent.com/251053501/530083449-6d0b0389-3686-425e-91e1-20c3f53fccb8.png?jwt=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3NjY2MDk5MzksIm5iZiI6MTc2NjYwOTYzOSwicGF0aCI6Ii8yNTEwNTM1MDEvNTMwMDgzNDQ5LTZkMGIwMzg5LTM2ODYtNDI1ZS05MWUxLTIwYzNmNTNmY2NiOC5wbmc_WC1BbXotQWxnb3JpdGhtPUFXUzQtSE1BQy1TSEEyNTYmWC1BbXotQ3JlZGVudGlhbD1BS0lBVkNPRFlMU0E1M1BRSzRaQSUyRjIwMjUxMjI0JTJGdXMtZWFzdC0xJTJGczMlMkZhd3M0X3JlcXVlc3QmWC1BbXotRGF0ZT0yMDI1MTIyNFQyMDUzNTlaJlgtQW16LUV4cGlyZXM9MzAwJlgtQW16LVNpZ25hdHVyZT02MjQ2M2JkYzlhMGE0Zjk4YjdhZjFhNjI1OGM0OTIxMWFjZGExMTM3YjRiOTk5N2ZkOGJiOTExOGZlMTA2M2FlJlgtQW16LVNpZ25lZEhlYWRlcnM9aG9zdCJ9.v2yni6oox6ZnwzJg4OXbxX9I-v1rZVIti2-n3vaEKQw">


