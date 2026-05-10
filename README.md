# DayZ P3D Binarizer

Простой drag-and-drop wrapper для DayZ Tools `binarize.exe`.

## Как пользоваться

1. Убедись, что DayZ Tools установлен и `P:\` смонтирован как project drive.
2. Перетащи исходный `MLOD` `.p3d` на `DayZ P3D Binarizer.exe`.
3. Готовый бинарный `ODOL` файл появится рядом с моделью в папке `_binarized`.

Если запустить `.exe` без аргументов, откроется окно выбора `.p3d`.

## Текстуры и материалы

Приложение не переписывает пути внутри модели. Перед запуском оно проверяет ссылки, найденные в `.p3d`, `.rvmat` и `.emat`.

По умолчанию используется изолированный project root: приложение копирует текущий аддон во временную папку и запускает `binarize.exe` с `-addon=<temp>\project`. Это защищает сборку от чужих сломанных `config.cpp` под `P:\`.

Для targeted-бинаризации приложение также подкладывает `model.cfg` и `skeleton.cfg`, если они лежат в той же папке, что и перетянутый `.p3d`.

Пример: если в модели указан путь:

```text
MyMod\data\body_co.paa
```

то файл должен существовать как:

```text
P:\MyMod\data\body_co.paa
```

Если ссылка отсутствует, приложение покажет предупреждение и спросит, продолжать ли бинаризацию.

## Настройка

Рядом с приложением лежит `p3d_binarizer_config.json`:

```json
{
  "project_root": "P:",
  "binarize_exe": "C:\\Program Files (x86)\\Steam\\steamapps\\common\\DayZ Tools\\Bin\\Binarize\\binarize.exe",
  "max_processes": 0,
  "output_folder_name": "_binarized",
  "isolated_project_root": true,
  "pause_on_exit": true,
  "continue_on_missing_references": false
}
```

`max_processes: 0` означает использовать число логических потоков CPU.


