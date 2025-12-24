# 🤖 Faceit Team Finder Bot

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
(https://github.com/nikfa35/faceit-team-bot/issues/1#issue-3760973644)

|Меню магазина|

|Пример выгрузки статистики в панели администратора|

|Пример системного уведомления об успешности/неуспешности рассылки на пользователей|

|Пример сообщения об ошибке от пользователя|

|Пример системного уведомления во время поиска тиммейтов|

|Пример отображения статистики пользователя|



