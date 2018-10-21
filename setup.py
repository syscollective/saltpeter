from setuptools import setup

setup(name='saltpeter',
      version='0.1.2',
      description='Distributed cron based on salt',
      url='http://github.com/syscollective/saltpeter',
      author='Marin Vintila',
      author_email='marin.vintila@syscollective.com',
      license='MIT',
      packages=['saltpeter'],
      entry_points = {
          'console_scripts': ['saltpeter=saltpeter.saltpeter:main'],
      },
      install_requires=[
          'salt',
          'crontab',
          'pyyaml'
      ],
      zip_safe=False)