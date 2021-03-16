from setuptools import setup

setup(name='saltpeter',
      version='0.2.4',
      description='Distributed cron based on salt',
      url='http://github.com/syscollective/saltpeter',
      author='Marin Vintila',
      author_email='marin.vintila@syscollective.com',
      license='MIT',
      packages=['saltpeter'],
      entry_points = {
          'console_scripts': ['saltpeter=saltpeter.main:main'],
      },
      install_requires=[
          'salt',
          'crontab',
          'pyyaml',
          'tornado',
          'elasticsearch'
      ],
      zip_safe=False)
