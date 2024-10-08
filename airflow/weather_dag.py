from airflow import DAG
from datetime import timedelta, datetime
from airflow.providers.http.sensors.http import HttpSensor
from airflow.providers.http.operators.http import SimpleHttpOperator
from airflow.operators.python import PythonOperator
import pandas as pd
import json
import os
import s3fs

# Función que convierte la temperatura de Kelvin a Fahrenheit.
def kelvin_to_fahrenheit(temp_in_kelvin):
    """
        Converts temperature from Kelvin to Fahrenheit.

        Parameters:
        temp_in_kelvin (float): Temperature in Kelvin.

        Returns:
        float: Temperature in Fahrenheit.
        """
    return (temp_in_kelvin - 273.15) * 9/5 + 32

# Función que transforma y carga los datos obtenidos.
def transform_load_data(task_instance):
    # Extrae los datos de XCom.
    data = task_instance.xcom_pull(task_ids="extract_weather_data")
    
    # Verificamos si los datos están disponibles.
    if not data:
        raise ValueError("No data received from extract_weather_data task")

    # Transformamos los datos.
    city = data["name"]
    weather_description = data["weather"][0]['description']
    temp_fahrenheit = kelvin_to_fahrenheit(data["main"]["temp"])
    feels_like_fahrenheit = kelvin_to_fahrenheit(data["main"]["feels_like"])
    min_temp_fahrenheit = kelvin_to_fahrenheit(data["main"]["temp_min"])
    max_temp_fahrenheit = kelvin_to_fahrenheit(data["main"]["temp_max"])
    pressure = data["main"]["pressure"]
    humidity = data["main"]["humidity"]
    wind_speed = data["wind"]["speed"]

    time_of_record = datetime.utcfromtimestamp(data['dt']) + timedelta(seconds=data['timezone'])
    sunrise_time = datetime.utcfromtimestamp(data['sys']['sunrise']) + timedelta(seconds=data['timezone'])
    sunset_time = datetime.utcfromtimestamp(data['sys']['sunset']) + timedelta(seconds=data['timezone'])

    transformed_data = {
        "City": city,
        "Description": weather_description,
        "Temperature (F)": temp_fahrenheit,
        "Feels Like (F)": feels_like_fahrenheit,
        "Min Temp (F)": min_temp_fahrenheit,
        "Max Temp (F)": max_temp_fahrenheit,
        "Pressure": pressure,
        "Humidity": humidity,
        "Wind Speed": wind_speed,
        "Time of Record": time_of_record,
        "Sunrise (Local Time)": sunrise_time,
        "Sunset (Local Time)": sunset_time
    }

    # Convertimos los datos transformados a DataFrame.
    df_data = pd.DataFrame([transformed_data])
    
    # Generamos el nombre del archivo.
    dt_string = datetime.utcnow().strftime("%d%m%Y%H%M%S")
    filename = f"current_weather_data_{city}_{dt_string}.csv"
    s3_path = f"s3://weather-data-engineering-project/{filename}"
    
    # Obtenemos las credenciales de AWS desde variables de entorno.
    aws_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
    aws_token = os.getenv("AWS_SESSION_TOKEN")

    # Validamos que las credenciales estén presentes.
    if not aws_key or not aws_secret:
        raise ValueError("AWS credentials are missing.")
    
    # Guardamos el DataFrame en S3 usando s3fs.
    fs = s3fs.S3FileSystem(key=aws_key, secret=aws_secret, token=aws_token)
    
    # Guardamos el DataFrame directamente en S3.
    with fs.open(s3_path, 'w') as f:
        df_data.to_csv(f, index=False)

# Definición de argumentos por defecto para el DAG.
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2024, 8, 1),
    'email': ['myemail@domain.com'],
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=2)
}

# Definición del DAG.
with DAG('weather_dag',
         default_args=default_args,
         schedule_interval='@daily',
         catchup=False) as dag:
    
    cityname = "Portland"  
    API_KEY = os.getenv("WEATHER_API_KEY")  # Debe ser configurado en el entorno.

    # Tarea que verifica si la API de clima está disponible.
    is_weather_api_ready = HttpSensor(
        task_id='is_weather_api_ready',
        http_conn_id='weathermap_api',
        endpoint=f'/data/2.5/weather?q={cityname}&APPID={API_KEY}'
    )

    # Tarea que extrae los datos del clima de la API.
    extract_weather_data = SimpleHttpOperator(
        task_id='extract_weather_data',
        http_conn_id='weathermap_api',
        endpoint=f'/data/2.5/weather?q={cityname}&APPID={API_KEY}',
        method='GET',
        response_filter=lambda r: json.loads(r.text),
        log_response=True
    )

    # Tarea que transforma los datos obtenidos y los carga en S3.
    transform_load_weather_data = PythonOperator(
        task_id='transform_load_weather_data',
        python_callable=transform_load_data
    )

    # Definimos el orden de ejecución de las tareas en el DAG.
    is_weather_api_ready >> extract_weather_data >> transform_load_weather_data