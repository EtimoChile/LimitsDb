## Instrucciones para ejecutar con Poetry

1. **Instalar Poetry**: Si no tienes Poetry instalado, puedes instalarlo siguiendo las instrucciones en [la documentación oficial de Poetry](https://python-poetry.org/docs/#installation).

2. **Instalar dependencias**: Ejecuta el siguiente comando en el directorio del proyecto para instalar las dependencias definidas en el archivo `pyproject.toml`:
    ```sh
    poetry install
    ```

3. **Ejecutar el proyecto**: Una vez instaladas las dependencias, puedes ejecutar el proyecto utilizando el script configurado en `pyproject.toml`:
    ```sh
    poetry run terminusdb
    ```

4. **Abrir un shell**: Si necesitas abrir un shell con el entorno virtual de Poetry activado, puedes hacerlo con:
    ```sh
    poetry shell
    ```

5. **Agregar nuevas dependencias**: Para agregar nuevas dependencias al proyecto, utiliza el siguiente comando:
    ```sh
    poetry add <nombre_de_la_dependencia>
    ```

6. **Actualizar dependencias**: Para actualizar las dependencias del proyecto, ejecuta:
    ```sh
    poetry update
    ```

Asegúrate de reemplazar `<nombre_del_script>` y `<nombre_de_la_dependencia>` con los nombres correspondientes a tu proyecto.