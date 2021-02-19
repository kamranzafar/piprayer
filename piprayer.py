import sys
import configparser
import praytimes

if __name__ == "__main__":
    if len(sys.argv) <= 1:
        print('Require configuration file as argument')
        exit(1)

    try:
        f = open(sys.argv[1])
    except IOError:
        print('File not found or is inaccessible')
    finally:
        f.close()

    config = configparser.ConfigParser()
    config.sections()
    config.read(sys.argv[1])

    if not config.has_section('Default'):
        print("Prayer configuration is missing")
        exit(1)

    prayerConfig = config['Default']

    # for key in prayerConfig:
    # 	print(key + ': '+prayerConfig[key])

    prayTimes = praytimes.PrayTimes(prayerConfig['method'], prayerConfig['asr-time'])

    from datetime import date

    times = prayTimes.getTimes(date.today(),
                               (float(prayerConfig['lat']),
                                float(prayerConfig['lng'])),
                               int(prayerConfig['gmt-offset']),
                               int(prayerConfig['dst']))

    for i in prayerConfig['prayers'].split(","):
        print(i + '-' + times[i.strip().lower()])
